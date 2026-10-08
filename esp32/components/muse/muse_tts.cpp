/*
 * OpenAI's speech endpoint streams PCM16 mono at 24 kHz. See
 * https://developers.openai.com/api/docs/guides/text-to-speech
 * TLS and bounded queue waits run here, never on the UI, voice or Muse socket
 * task. Every packet is tagged, so cancellation cannot play an old reply.
 */
#include "muse_tts.h"
#include "muse_tts_pcm.h"

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <strings.h>

#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "mbedtls/platform_util.h"
#include "nvs.h"

static const char *TAG = "muse_tts";
static const char *NS = "muse_tts";
static const int64_t JOB_TIMEOUT_US = 120 * 1000000LL;
static const int64_t STALL_TIMEOUT_US = 15 * 1000000LL;

struct request_t { uint32_t id; char *text; };
struct packet_t { uint32_t id; int frames; int16_t pcm[MUSE_TTS_FRAMES]; };
static QueueHandle_t s_requests, s_packets;
static std::atomic<uint32_t> s_generation{0};

static esp_err_t response_header(esp_http_client_event_t *event)
{
    if (event->event_id == HTTP_EVENT_ON_HEADER && event->header_key && event->header_value &&
        !strcasecmp(event->header_key, "Content-Type")) {
        bool pcm = !strncasecmp(event->header_value, "audio/pcm", 9) ||
                   !strncasecmp(event->header_value, "application/octet-stream", 24);
        *static_cast<bool *>(event->user_data) = pcm;
    }
    return ESP_OK;
}

static esp_err_t read_key(char *out, size_t *len)
{
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(NS, NVS_READONLY, &nvs);
    if (err == ESP_OK) {
        err = nvs_get_str(nvs, "api_key", out, len);
        nvs_close(nvs);
    }
    return err;
}

extern "C" bool muse_tts_configured(void)
{
    size_t len = 0;
    return read_key(nullptr, &len) == ESP_OK && len > 1 && len <= MUSE_TTS_KEY_MAX + 1;
}

extern "C" esp_err_t muse_tts_set_key(const char *key)
{
    if (!key || strlen(key) > MUSE_TTS_KEY_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    for (const char *p = key; *p; p++) {
        if ((unsigned char)*p < 33 || (unsigned char)*p > 126) {
            return ESP_ERR_INVALID_ARG;   /* no whitespace or header injection */
        }
    }
    nvs_handle_t nvs;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &nvs);
    if (err != ESP_OK) {
        return err;
    }
    err = *key ? nvs_set_str(nvs, "api_key", key) : nvs_erase_key(nvs, "api_key");
    if (!*key && err == ESP_ERR_NVS_NOT_FOUND) {
        err = ESP_OK;
    }
    if (err == ESP_OK) {
        err = nvs_commit(nvs);
    }
    nvs_close(nvs);
    return err;
}

static bool current(uint32_t id, int64_t deadline)
{
    return id == s_generation.load() && esp_timer_get_time() < deadline;
}

static bool send_packet(const packet_t &packet, int64_t deadline)
{
    while (current(packet.id, deadline)) {
        if (xQueueSend(s_packets, &packet, pdMS_TO_TICKS(20)) == pdTRUE) {
            return true;
        }
    }
    return false;
}

static bool fetch_chunk(uint32_t id, const char *text, size_t len, int64_t deadline)
{
    char key[MUSE_TTS_KEY_MAX + 1] = {};
    char auth[MUSE_TTS_KEY_MAX + 8] = {};
    size_t key_len = sizeof(key);
    if (read_key(key, &key_len) != ESP_OK || !key[0]) {
        return false;
    }
    snprintf(auth, sizeof(auth), "Bearer %s", key);
    mbedtls_platform_zeroize(key, sizeof(key));

    char *input = static_cast<char *>(heap_caps_malloc(len + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!input) {
        mbedtls_platform_zeroize(auth, sizeof(auth));
        return false;
    }
    memcpy(input, text, len);
    input[len] = '\0';
    cJSON *json = cJSON_CreateObject();
    bool built = json && cJSON_AddStringToObject(json, "model", MUSE_TTS_MODEL) &&
                 cJSON_AddStringToObject(json, "voice", MUSE_TTS_VOICE) &&
                 cJSON_AddStringToObject(json, "input", input) &&
                 cJSON_AddStringToObject(json, "response_format", "pcm") &&
                 cJSON_AddStringToObject(json, "instructions", "Speak warmly and clearly at a natural conversational pace.");
    char *body = built ? cJSON_PrintUnformatted(json) : nullptr;
    cJSON_Delete(json);
    free(input);

    esp_http_client_config_t cfg = {};
    cfg.url = "https://api.openai.com/v1/audio/speech";
    cfg.method = HTTP_METHOD_POST;
    cfg.crt_bundle_attach = esp_crt_bundle_attach;
    cfg.timeout_ms = 15000;
    cfg.buffer_size = 2048;
    cfg.buffer_size_tx = 1024;
    cfg.disable_auto_redirect = true;   /* the bearer key only goes to OpenAI */
    bool is_pcm = false;
    cfg.event_handler = response_header;
    cfg.user_data = &is_pcm;
    esp_http_client_handle_t client = body ? esp_http_client_init(&cfg) : nullptr;
    bool ok = false;
    size_t frames = 0;
    int64_t started = esp_timer_get_time();
    if (client) {
        esp_err_t headers = esp_http_client_set_header(client, "Authorization", auth);
        if (headers == ESP_OK) {
            headers = esp_http_client_set_header(client, "Content-Type", "application/json");
        }
        if (headers == ESP_OK && current(id, deadline) && esp_http_client_open(client, strlen(body)) == ESP_OK) {
            size_t sent = 0, size = strlen(body);
            while (sent < size && current(id, deadline)) {
                int n = esp_http_client_write(client, body + sent, size - sent);
                if (n <= 0) {
                    break;
                }
                sent += n;
            }
            if (sent == size && current(id, deadline) && esp_http_client_fetch_headers(client) >= 0) {
                int status = esp_http_client_get_status_code(client);
                if (status == 200 && is_pcm) {
                    esp_http_client_set_timeout_ms(client, 1000);
                    uint8_t bytes[MUSE_TTS_FRAMES * 2];
                    muse_tts_pcm_t decoder = {};
                    packet_t packet = {};
                    packet.id = id;
                    int64_t progress = esp_timer_get_time();
                    while (current(id, deadline) && esp_timer_get_time() - progress < STALL_TIMEOUT_US) {
                        int n = esp_http_client_read(client, reinterpret_cast<char *>(bytes), sizeof(bytes));
                        if (n == -ESP_ERR_HTTP_EAGAIN) {
                            continue;
                        }
                        if (n < 0) {
                            break;
                        }
                        if (n == 0) {
                            ok = frames > 0 && !decoder.pending && esp_http_client_is_complete_data_received(client);
                            break;
                        }
                        packet.frames = muse_tts_pcm_decode(&decoder, bytes, n, packet.pcm);
                        if (packet.frames) {
                            if (!frames) {
                                ESP_LOGI(TAG, "first PCM in %d ms", (int)((esp_timer_get_time() - started) / 1000));
                            }
                            if (!send_packet(packet, deadline)) {
                                break;
                            }
                            frames += packet.frames;
                        }
                        progress = esp_timer_get_time();
                    }
                } else {
                    /* Never log response bodies, headers or the credential. */
                    ESP_LOGW(TAG, "speech HTTP %d%s", status, status == 200 ? " (unexpected content type)" : "");
                }
            }
        }
        esp_http_client_cleanup(client);
    }
    mbedtls_platform_zeroize(auth, sizeof(auth));
    cJSON_free(body);
    ESP_LOGI(TAG, "speech %s: %u frames at 24000 Hz", ok ? "received" : "stopped", (unsigned)frames);
    return ok;
}

static void worker(void *)
{
    request_t request;
    for (;;) {
        xQueueReceive(s_requests, &request, portMAX_DELAY);
        int64_t deadline = esp_timer_get_time() + JOB_TIMEOUT_US;
        bool ok = true;
        size_t len = strlen(request.text), off = 0;
        while (off < len && current(request.id, deadline)) {
            size_t n = muse_tts_text_chunk(request.text + off, len - off);
            if (!fetch_chunk(request.id, request.text + off, n, deadline)) {
                ok = false;
                break;
            }
            off += n;
        }
        free(request.text);
        packet_t end = {};
        end.id = request.id;
        end.frames = ok && off == len ? -1 : -2;
        /* Report timeouts too. A cancelled job cannot enqueue into a new one. */
        send_packet(end, esp_timer_get_time() + STALL_TIMEOUT_US);
    }
}

static bool init(void)
{
    if (s_requests) {
        return true;
    }
    s_requests = xQueueCreateWithCaps(1, sizeof(request_t), MALLOC_CAP_SPIRAM);
    s_packets = xQueueCreateWithCaps(32, sizeof(packet_t), MALLOC_CAP_SPIRAM);
    if (s_requests && s_packets &&
        xTaskCreatePinnedToCoreWithCaps(worker, "muse_tts", 16 * 1024, nullptr, 4, nullptr, 0,
                                      MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) == pdPASS) {
        return true;
    }
    if (s_requests) { vQueueDeleteWithCaps(s_requests); }
    if (s_packets) { vQueueDeleteWithCaps(s_packets); }
    s_requests = s_packets = nullptr;
    return false;
}

extern "C" void muse_tts_cancel(void)
{
    ++s_generation;
    if (s_packets) {
        xQueueReset(s_packets);
    }
}

extern "C" uint32_t muse_tts_begin(const char *text)
{
    muse_tts_cancel();
    if (!text || !*text || !muse_tts_configured() || !init()) {
        return 0;
    }
    char *copy = static_cast<char *>(heap_caps_malloc(strlen(text) + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!copy) {
        return 0;
    }
    strcpy(copy, text);
    request_t old;
    if (xQueueReceive(s_requests, &old, 0) == pdTRUE) {
        free(old.text);
    }
    request_t request = { s_generation.load(), copy };
    if (xQueueSend(s_requests, &request, 0) != pdTRUE) {
        free(copy);
        return 0;
    }
    return request.id;
}

extern "C" int muse_tts_read(uint32_t job, int16_t *pcm)
{
    packet_t packet;
    if (!s_packets || job != s_generation.load()) {
        return -2;
    }
    while (xQueueReceive(s_packets, &packet, 0) == pdTRUE) {
        if (packet.id != job) {
            continue;
        }
        if (packet.frames > 0) {
            memcpy(pcm, packet.pcm, packet.frames * sizeof(int16_t));
        }
        return packet.frames;
    }
    return 0;
}
