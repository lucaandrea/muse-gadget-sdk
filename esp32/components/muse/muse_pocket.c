#include "muse_pocket.h"
#include "muse_pocket_filename.h"
#include "muse_audio.h"
#include "muse_cards.h"
#include "muse_input.h"
#include "muse_mem.h"
#include "muse_settings.h"
#include "muse_state.h"
#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_partition.h"
#include "esp_spiffs.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "esp_wifi.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#if CONFIG_MUSE_REFINED_UI
#include "muse_reply.h"
#endif
#include "nvs.h"
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define FS "/pocket"
#define NOTE_BYTES (16000 * 2 * 15)
#define NOTE_LIMIT 3
#define AUDIO_BYTES 65536
#define RX_BYTES 32768
typedef struct { int kind; char *data; size_t len; char id[65]; } work_t;
enum { SAVE_NOTE, ACK_NOTE, CACHE_SYNC, SEND_ACTION, STORAGE_TEST };
static const char *TAG = "pocket";
static char s_url[257], s_token[129], *s_ca, s_headers[160];
#define CA_BYTES 4097
static esp_websocket_client_handle_t s_ws;
static QueueHandle_t s_work;
static SemaphoreHandle_t s_send, s_audio_lock;
static unsigned char *s_audio, *s_rx;
static size_t s_audio_read, s_audio_size, s_rx_len, s_rx_expected;
static int s_rx_opcode;
static volatile bool s_connected, s_recording, s_inflight, s_ready, s_done, s_fs;
static volatile bool s_restart_ws;
static volatile uint32_t s_generation;
static volatile uint32_t s_audio_received, s_audio_played, s_audio_errors;
static volatile int64_t s_retry_at;
static char s_note_id[65];
static cJSON *s_reminders;
static time_t s_server_time;
static int64_t s_synced_at;

_Static_assert(CONFIG_SPIFFS_OBJ_NAME_LEN >= MUSE_POCKET_NOTE_SPIFFS_SIZE,
               "Pocket recording and temporary names must fit SPIFFS");

static bool note_path(const char *id, char path[100])
{
    char name[MUSE_POCKET_NOTE_FILENAME_SIZE];
    path[0] = 0;
    if (!muse_pocket_note_filename(id, name)) return false;
    snprintf(path, 100, FS "/%s", name);
    return true;
}

static const char *str(cJSON *o, const char *key)
{
    cJSON *v = cJSON_GetObjectItemCaseSensitive(o, key);
    return cJSON_IsString(v) ? v->valuestring : "";
}
static void random_id(char out[33])
{
    unsigned char bytes[16]; esp_fill_random(bytes, sizeof(bytes));
    for (int i = 0; i < 16; ++i) snprintf(out + 2*i, 3, "%02x", bytes[i]);
}
static bool safe_id(const char *id)
{
    if (strlen(id) != 32) return false;
    for (int i = 0; i < 32; ++i) if (!((id[i] >= '0' && id[i] <= '9') || (id[i] >= 'a' && id[i] <= 'f'))) return false;
    return true;
}
static bool send_json(cJSON *o)
{
    bool ok = false;
    cJSON_AddNumberToObject(o, "v", 1);
    char *text = cJSON_PrintUnformatted(o);
    if (text && s_connected && xSemaphoreTake(s_send, pdMS_TO_TICKS(2000))) {
        ok = esp_websocket_client_send_text(s_ws, text, strlen(text), pdMS_TO_TICKS(2000)) == (int)strlen(text);
        xSemaphoreGive(s_send);
    }
    free(text); cJSON_Delete(o); return ok;
}
static cJSON *event(const char *type)
{
    cJSON *o = cJSON_CreateObject(); cJSON_AddStringToObject(o, "type", type); return o;
}
static void clear_audio(void)
{
    xSemaphoreTake(s_audio_lock, portMAX_DELAY); s_audio_size = 0; s_audio_read = 0; xSemaphoreGive(s_audio_lock);
}
static void card_from_json(cJSON *o)
{
    muse_card_t *card = heap_caps_calloc(1, sizeof(*card), MUSE_BIG_CAPS);
    if (!card) return;
    #define COPY_FIELD(key) snprintf(card->key, sizeof(card->key), "%s", str(o, #key))
    COPY_FIELD(id); COPY_FIELD(kind); COPY_FIELD(title); COPY_FIELD(body); COPY_FIELD(source); COPY_FIELD(status);
    #undef COPY_FIELD
    cJSON *buttons = cJSON_GetObjectItemCaseSensitive(o, "buttons");
    for (int i = 0; i < 3; ++i) {
        cJSON *b = cJSON_GetArrayItem(buttons, i);
        snprintf(card->buttons[i].label, sizeof(card->buttons[i].label), "%s", str(b, "label"));
        snprintf(card->buttons[i].action, sizeof(card->buttons[i].action), "%s", str(b, "action"));
    }
    muse_cards_submit(card);
    if (!strcmp(card->kind, "reminder") && !strcmp(card->status, "due")) {
        muse_state_set_asleep(false); muse_state_set_caption("Reminder: %s", card->title);
    }
    free(card);
}
static void receive_json(const char *text)
{
    cJSON *o = cJSON_Parse(text); if (!o) return;
    const char *type = str(o, "type");
    cJSON *epoch = cJSON_GetObjectItemCaseSensitive(o, "generation");
    bool current = !epoch || (uint32_t)epoch->valuedouble == s_generation;
    if (!strcmp(type, "card")) card_from_json(cJSON_GetObjectItem(o, "card"));
    else if (!strcmp(type, "card.remove")) muse_cards_remove(str(o, "id"));
    else if (!strcmp(type, "cards.reset")) muse_cards_clear();
    else if (!strcmp(type, "memory.invalidated")) muse_cards_invalidate_memories();
    else if (!strcmp(type, "voice.ready") && current) s_ready = true;
    else if (!strcmp(type, "voice.done") && current) { s_done = true; s_inflight = false; }
    else if (!strcmp(type, "caption") && current) {
        muse_state_set_caption("%s", str(o, "text"));
#if CONFIG_MUSE_REFINED_UI
        muse_reply_publish(str(o, "text"), 0);
#endif
    }
    else if (!strcmp(type, "voice.received") && safe_id(str(o, "id"))) {
        work_t w = {.kind=ACK_NOTE}; snprintf(w.id, sizeof(w.id), "%s", str(o, "id"));
        xQueueSend(s_work, &w, 0);
    } else if (!strcmp(type, "sync")) {
        char *copy = cJSON_PrintUnformatted(o);
        work_t w = {.kind=CACHE_SYNC, .data=copy};
        if (!copy || !xQueueSend(s_work, &w, 0)) free(copy);
    } else if (!strcmp(type, "action.result")) {
        muse_cards_remove(str(o, "id")); muse_state_set_caption("Action confirmed");
    } else if (!strcmp(type, "error")) {
        muse_state_set_caption("%s", str(o, "text"));
        s_inflight = false; s_done = true;
        s_retry_at = esp_timer_get_time() + 300000000; /* preserve the note; avoid an error/retry loop */
    }
    cJSON_Delete(o);
}
static void receive_binary(const unsigned char *data, size_t len)
{
    uint32_t epoch; if (len < 6 || (len & 1)) return; memcpy(&epoch, data, 4);
    if (epoch != s_generation || s_recording) return;
    data += 4; len -= 4;
    xSemaphoreTake(s_audio_lock, portMAX_DELAY);
    if (len <= AUDIO_BYTES - s_audio_size) {
        size_t at = (s_audio_read + s_audio_size) % AUDIO_BYTES;
        size_t first = len < AUDIO_BYTES - at ? len : AUDIO_BYTES - at;
        memcpy(s_audio + at, data, first); memcpy(s_audio, data + first, len - first); s_audio_size += len;
        s_audio_received += len / sizeof(int16_t);
        muse_state_set_asleep(false); muse_state_nudge();
    } else { /* Never play corrupted or discontinuous speech as a successful reply. */
        s_audio_size = 0; muse_state_set_caption("Audio buffer full. Read the reply in the companion.");
    }
    xSemaphoreGive(s_audio_lock);
}
static void ws_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg; (void)base;
    esp_websocket_event_data_t *d = data;
    if (id == WEBSOCKET_EVENT_CONNECTED) { s_connected = true; s_retry_at = 0; ESP_LOGI(TAG, "companion connected"); }
    else if (id == WEBSOCKET_EVENT_DISCONNECTED || id == WEBSOCKET_EVENT_ERROR || id == WEBSOCKET_EVENT_CLOSED) {
        s_connected = false; s_ready = false; s_inflight = false; s_done = true; s_rx_len = 0;
        if (id == WEBSOCKET_EVENT_CLOSED) s_restart_ws = true;
    } else if (id == WEBSOCKET_EVENT_DATA) {
        if (d->op_code == 8 || d->op_code == 9 || d->op_code == 10) return;
        if (d->payload_offset == 0) {
            if (d->op_code == 1 || d->op_code == 2) { s_rx_len = 0; s_rx_opcode = d->op_code; }
            s_rx_expected = s_rx_len + d->payload_len;
        }
        if (d->data_len < 0 || s_rx_expected >= RX_BYTES || s_rx_len + d->data_len >= RX_BYTES) { s_rx_len = 0; return; }
        memcpy(s_rx + s_rx_len, d->data_ptr, d->data_len); s_rx_len += d->data_len;
        if (d->fin && d->payload_offset + d->data_len == d->payload_len) {
            s_rx[s_rx_len] = 0;
            if (s_rx_opcode == 1) receive_json((char *)s_rx); else if (s_rx_opcode == 2) receive_binary(s_rx, s_rx_len);
            s_rx_len = 0;
        }
    }
}
static void load_nvs(void)
{
    nvs_handle_t n; if (nvs_open("muse_pocket", NVS_READONLY, &n) != ESP_OK) return;
    size_t len = sizeof(s_url); nvs_get_str(n, "url", s_url, &len);
    len = sizeof(s_token); nvs_get_str(n, "token", s_token, &len);
    len = CA_BYTES; nvs_get_str(n, "ca", s_ca, &len); nvs_close(n);
}
void muse_pocket_init(void)
{
    s_ca = heap_caps_calloc(1, CA_BYTES, MUSE_BIG_CAPS);
    if (!s_ca) return;
    load_nvs(); muse_cards_init();
    s_audio = heap_caps_malloc(AUDIO_BYTES, MUSE_BIG_CAPS); s_rx = heap_caps_malloc(RX_BYTES, MUSE_BIG_CAPS);
    s_work = xQueueCreate(10, sizeof(work_t)); s_send = xSemaphoreCreateMutex(); s_audio_lock = xSemaphoreCreateMutex();
    if (!s_audio || !s_rx || !s_work || !s_send || !s_audio_lock) { s_url[0] = 0; ESP_LOGE(TAG, "out of memory"); }
}
bool muse_pocket_enabled(void) { return !strncmp(s_url, "wss://", 6) && s_token[0]; }
bool muse_pocket_busy(void) { return s_recording || s_inflight || muse_pocket_audio_pending(); }
bool muse_pocket_audio_pending(void) { return s_audio_size > 0; }
void muse_pocket_play_chunk(void)
{
    int16_t pcm[MUSE_AUDIO_CHUNK];
    xSemaphoreTake(s_audio_lock, portMAX_DELAY);
    size_t bytes = s_audio_size < sizeof(pcm) ? s_audio_size : sizeof(pcm);
    size_t first = bytes < AUDIO_BYTES - s_audio_read ? bytes : AUDIO_BYTES - s_audio_read;
    memcpy(pcm, s_audio + s_audio_read, first); memcpy((char *)pcm + first, s_audio, bytes - first);
    s_audio_read = (s_audio_read + bytes) % AUDIO_BYTES; s_audio_size -= bytes;
    xSemaphoreGive(s_audio_lock);
    if (bytes) {
        muse_state_set_mode(MUSE_MODE_SPEAKING); muse_state_set_level(muse_audio_level(pcm, bytes/2));
        if (muse_settings_speaker_on()) {
            esp_err_t err = muse_audio_write(pcm, bytes/2);
            if (err == ESP_OK) s_audio_played += bytes / sizeof(int16_t);
            else {
                if (!s_audio_errors++) ESP_LOGE(TAG, "speaker write failed: %s", esp_err_to_name(err));
                muse_state_set_caption("Speaker unavailable - check Sound settings");
            }
        } else vTaskDelay(pdMS_TO_TICKS(20));
    }
    if (s_done && !s_audio_size) { muse_state_set_mode(MUSE_MODE_IDLE); muse_state_set_level(0); }
}
void muse_pocket_record(QueueHandle_t input)
{
    s_recording = true; s_generation++; s_inflight = false; s_done = false; clear_audio();
    if (s_connected) send_json(event("voice.cancel"));
    unsigned char *pcm = heap_caps_malloc(NOTE_BYTES, MUSE_BIG_CAPS);
    if (!pcm) { s_recording = false; muse_state_set_caption("Not enough memory to record"); return; }
    size_t used = 0; muse_state_set_mode(MUSE_MODE_LISTENING); muse_state_set_caption("Listening · release to save");
    while (used + MUSE_AUDIO_CHUNK * 2 <= NOTE_BYTES) {
        muse_input_event_t e;
        if (xQueueReceive(input, &e, 0) && e.type == MUSE_PTT_UP) break;
        if (muse_audio_read((int16_t *)(pcm + used), MUSE_AUDIO_CHUNK) != ESP_OK) break;
        muse_state_set_level(muse_audio_level((int16_t *)(pcm + used), MUSE_AUDIO_CHUNK));
        used += MUSE_AUDIO_CHUNK * 2; muse_state_set_progress((float)used / NOTE_BYTES);
    }
    s_recording = false; muse_state_set_level(0); muse_state_set_progress(0); muse_state_set_mode(MUSE_MODE_IDLE);
    if (used < 16000 / 2) { free(pcm); muse_state_set_caption("Hold longer to talk"); return; }
    work_t w = {.kind=SAVE_NOTE, .data=(char *)pcm, .len=used}; random_id(w.id);
    if (!xQueueSend(s_work, &w, 0)) { free(pcm); muse_state_set_caption("Queue busy · capture was not saved"); }
    else muse_state_set_caption("Saving your capture...");
}
static int notes(char first[65])
{
    first[0] = 0;
    DIR *dir = opendir(FS); if (!dir) return -1;
    int count = 0; struct dirent *e;
    while ((e = readdir(dir))) {
        char id[33];
        if (!muse_pocket_note_id(e->d_name, id)) continue;
        /* Compare original sequence-prefixed IDs, not base64's alphabet. */
        if (!count || strcmp(id, first) < 0) snprintf(first, 65, "%s", id);
        count++;
    }
    closedir(dir); return count;
}
static bool write_atomic(const char *name, const void *data, size_t len)
{
    char temp[100]; snprintf(temp, sizeof(temp), "%s.tmp", name);
    FILE *f = fopen(temp, "wb");
    if (!f) { ESP_LOGE(TAG, "open storage file failed: errno %d (%s)", errno, strerror(errno)); return false; }
    bool ok = fwrite(data, 1, len, f) == len && fflush(f) == 0;
    int error = ok ? 0 : errno;
    if (ok) ok = fsync(fileno(f)) == 0;
    if (!ok && !error) error = errno;
    if (fclose(f)) { if (!error) error = errno; ok = false; }
    if (ok) ok = rename(temp, name) == 0;
    if (!ok) {
        if (!error) error = errno;
        ESP_LOGE(TAG, "commit storage file failed: errno %d (%s)", error, strerror(error));
        unlink(temp);
        errno = error;
    }
    return ok;
}

/* Exercises the actual maximum-length temporary name, flush, rename and read
 * path. The .chk suffix keeps this diagnostic out of the upload queue. */
static void storage_test(void)
{
    char id[33], path[100];
    random_id(id);
    note_path(id, path);
    memcpy(path + strlen(path) - 4, ".chk", 5);
    unsigned char expected[257], actual[257];
    for (size_t i = 0; i < sizeof(expected); i++) expected[i] = (unsigned char)(i * 37);
    bool ok = s_fs && write_atomic(path, expected, sizeof(expected));
    if (ok) {
        FILE *f = fopen(path, "rb");
        ok = f && fread(actual, 1, sizeof(actual), f) == sizeof(actual) &&
             memcmp(actual, expected, sizeof(actual)) == 0;
        if (f && fclose(f)) ok = false;
    }
    if (s_fs && unlink(path) && errno != ENOENT) ok = false;
    size_t total = 0, used = 0;
    esp_spiffs_info("pocket", &total, &used);
    printf("@pocket.storage {\"ok\":%s,\"total\":%u,\"used\":%u}\n",
           ok ? "true" : "false", (unsigned)total, (unsigned)used);
    fflush(stdout);
    muse_state_set_caption("%s", ok ? "Storage ready - hold to talk" : "Storage check failed");
}
static void update_cache(const char *data, bool persist)
{
    cJSON *o = cJSON_Parse(data); if (!o) return;
    bool changed = !cJSON_Compare(s_reminders, cJSON_GetObjectItem(o, "reminders"), true);
    cJSON_Delete(s_reminders); s_reminders = cJSON_Duplicate(cJSON_GetObjectItem(o, "reminders"), true);
    cJSON *t = cJSON_GetObjectItem(o, "server_epoch");
    if (persist && cJSON_IsNumber(t)) { s_server_time = (time_t)t->valuedouble; s_synced_at = esp_timer_get_time(); }
    if (persist && changed && s_fs) write_atomic(FS "/reminders.json", data, strlen(data));
    cJSON_Delete(o);
}
static void check_reminders(void)
{
    time_t now = s_server_time ? s_server_time + (esp_timer_get_time() - s_synced_at) / 1000000 : time(NULL);
    if (now < 1760000000) return; /* Clock unknown after an offline cold boot: do not invent a due time. */
    cJSON *r;
    cJSON_ArrayForEach(r, s_reminders) {
        cJSON *due = cJSON_GetObjectItem(r, "due");
        if (!cJSON_IsNumber(due) || due->valuedouble > now || strcmp(str(r, "state"), "scheduled") || str(r, "ssid")[0]) continue;
        cJSON_ReplaceItemInObject(r, "state", cJSON_CreateString("due"));
        muse_card_t *card = heap_caps_calloc(1, sizeof(*card), MUSE_BIG_CAPS); if (!card) continue;
        snprintf(card->id, sizeof(card->id), "%s", str(r, "id")); snprintf(card->kind, sizeof(card->kind), "reminder");
        snprintf(card->title, sizeof(card->title), "%s", str(r, "title")); snprintf(card->body, sizeof(card->body), "Reminder due. Reconnect to sync acknowledgment.");
        snprintf(card->status, sizeof(card->status), "due"); snprintf(card->source, sizeof(card->source), "Saved reminder");
        snprintf(card->buttons[0].label, 25, "Done"); snprintf(card->buttons[0].action, 16, "done");
        snprintf(card->buttons[1].label, 25, "Snooze"); snprintf(card->buttons[1].action, 16, "snooze");
        muse_cards_submit(card); muse_state_set_asleep(false); muse_state_set_caption("Reminder: %s", card->title); free(card);
    }
}
static void upload_note(const char *id)
{
    char path[100]; if (!note_path(id, path)) return;
    FILE *f = fopen(path, "rb"); if (!f) return;
    s_generation++; s_ready = false; s_done = false; s_inflight = true;
    snprintf(s_note_id, sizeof(s_note_id), "%s", id);
    uint32_t epoch = s_generation;
    cJSON *o = event("voice.begin"); cJSON_AddStringToObject(o, "id", id); cJSON_AddStringToObject(o, "mode", "recorded"); cJSON_AddNumberToObject(o, "generation", epoch);
    bool ok = send_json(o);
    for (int i = 0; ok && !s_ready && s_connected && !s_recording && i < 100; ++i) vTaskDelay(pdMS_TO_TICKS(20));
    ok = ok && s_ready;
    unsigned char packet[1284]; memcpy(packet, &epoch, 4); size_t n;
    while (ok && s_connected && !s_recording && epoch == s_generation && (n = fread(packet + 4, 1, sizeof(packet) - 4, f))) {
        if (xSemaphoreTake(s_send, pdMS_TO_TICKS(2000))) {
            ok = esp_websocket_client_send_bin(s_ws, (char *)packet, n + 4, pdMS_TO_TICKS(2000)) == (int)n + 4;
            xSemaphoreGive(s_send);
        } else ok = false;
        vTaskDelay(1);
    }
    fclose(f);
    if (ok && epoch == s_generation && !s_recording) {
        ok = send_json(event("voice.end"));
        if (ok) { muse_state_set_mode(MUSE_MODE_THINKING); muse_state_set_caption("Working on your capture..."); }
    } else ok = false;
    if (!ok) s_inflight = false;
    s_retry_at = esp_timer_get_time() + 30000000;
}
static void worker(void *unused)
{
    (void)unused;
    esp_vfs_spiffs_conf_t fs = {.base_path=FS, .partition_label="pocket", .max_files=5, .format_if_mount_failed=false};
    s_fs = esp_vfs_spiffs_register(&fs) == ESP_OK;
    if (!s_fs) {
        /* Initialize only erased storage. A damaged existing queue needs recovery,
         * never an automatic format that silently destroys captures. */
        const esp_partition_t *p = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_SPIFFS, "pocket");
        unsigned char probe[256]; bool blank = p != NULL;
        for (size_t at = 0; blank && at < p->size; at += sizeof(probe)) {
            blank = esp_partition_read(p, at, probe, sizeof(probe)) == ESP_OK;
            for (size_t i = 0; blank && i < sizeof(probe); ++i) blank = probe[i] == 0xff;
        }
        if (blank && esp_spiffs_format("pocket") == ESP_OK) s_fs = esp_vfs_spiffs_register(&fs) == ESP_OK;
    }
    if (!s_fs) ESP_LOGE(TAG, "pocket partition unavailable: captures cannot be saved");
    FILE *cache = s_fs ? fopen(FS "/reminders.json", "rb") : NULL;
    if (cache) {
        char *data = heap_caps_calloc(1, 8192, MUSE_BIG_CAPS);
        if (data) { fread(data, 1, 8191, cache); update_cache(data, false); free(data); } fclose(cache);
    }
    snprintf(s_headers, sizeof(s_headers), "Authorization: Bearer %s\r\n", s_token);
    esp_websocket_client_config_t config = {.uri=s_url, .headers=s_headers, .task_stack=8192, .buffer_size=2048, .network_timeout_ms=5000, .reconnect_timeout_ms=10000, .ping_interval_sec=15};
    if (s_ca[0]) config.cert_pem = s_ca; else config.crt_bundle_attach = esp_crt_bundle_attach;
    s_ws = esp_websocket_client_init(&config);
    if (s_ws) { esp_websocket_register_events(s_ws, WEBSOCKET_EVENT_ANY, ws_event, NULL); esp_websocket_client_start(s_ws); }
    int64_t ping_at = 0;
    for (;;) {
        work_t w;
        if (xQueueReceive(s_work, &w, pdMS_TO_TICKS(1000))) {
            char path[100], first[65];
            if (w.kind == SAVE_NOTE) {
                int queued = s_fs ? notes(first) : -1;
                bool ok = queued >= 0 && queued < NOTE_LIMIT;
                nvs_handle_t n;
                if (ok && nvs_open("muse_pocket",NVS_READWRITE,&n)==ESP_OK) {
                    uint64_t seq=0; nvs_get_u64(n,"note_seq",&seq); seq++;
                    ok = nvs_set_u64(n,"note_seq",seq)==ESP_OK && nvs_commit(n)==ESP_OK;
                    nvs_close(n);
                    char prefix[17]; snprintf(prefix,sizeof(prefix),"%016llx",(unsigned long long)seq); memcpy(w.id,prefix,16);
                    ok = ok && note_path(w.id, path);
                } else ok=false;
                ok = ok && write_atomic(path, w.data, w.len);
                ESP_LOGI(TAG, "capture save: %s (%u bytes, %d already queued)", ok ? "ok" : "failed", (unsigned)w.len, queued);
                const char *message = ok ? "Capture saved · waiting for companion" :
                    queued < 0 ? "Storage unavailable - capture NOT saved" :
                    queued >= NOTE_LIMIT ? "Queue full - new capture NOT saved" : "Save failed - capture NOT saved";
                muse_state_set_caption("%s", message);
            } else if (w.kind == ACK_NOTE && note_path(w.id, path)) {
                if (!unlink(path)) ESP_LOGI(TAG, "capture acknowledged and removed");
            }
            else if (w.kind == CACHE_SYNC) update_cache(w.data, true);
            else if (w.kind == SEND_ACTION) { cJSON *o = cJSON_Parse(w.data); if (o && !send_json(o)) muse_state_set_caption("Action not sent · try again when connected"); }
            else if (w.kind == STORAGE_TEST) storage_test();
            free(w.data);
        }
        check_reminders();
        if (s_restart_ws && s_ws) {
            /* The IDF client stops after a graceful server close. Transport
             * errors reconnect themselves; a clean backend restart needs start. */
            s_restart_ws=false;
            vTaskDelay(pdMS_TO_TICKS(1000));
            if (esp_websocket_client_start(s_ws) != ESP_OK) s_restart_ws=true;
        }
        if (s_done && !s_audio_size && !s_recording && muse_state_mode(NULL) != MUSE_MODE_IDLE) muse_state_set_mode(MUSE_MODE_IDLE);
        if (!s_connected) continue;
        int64_t now = esp_timer_get_time();
        if (now > ping_at) {
            send_json(event("ping")); wifi_ap_record_t ap;
            if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) { cJSON *o = event("network"); cJSON_AddStringToObject(o, "ssid", (char *)ap.ssid); send_json(o); }
            ping_at = now + 15000000;
        }
        if (!s_recording && !s_inflight && !s_audio_size && now > s_retry_at && s_fs) { char id[65]; if (notes(id) > 0) upload_note(id); }
    }
}
void muse_pocket_start(void)
{
    /* Internal stack is required for SPIFFS/NVS flash writes. */
    if (muse_pocket_enabled() && xTaskCreate(worker, "muse_pocket", 8192, NULL, 4, NULL) != pdPASS) {
        s_url[0] = 0; ESP_LOGE(TAG, "worker allocation failed");
    }
}
bool muse_pocket_action(const char *id, const char *action)
{
    if (!s_connected) return false;
    char operation[33]; random_id(operation);
    cJSON *o = event("action"); cJSON_AddStringToObject(o, "id", id); cJSON_AddStringToObject(o, "action", action); cJSON_AddStringToObject(o, "operation_id", operation);
    work_t w = {.kind=SEND_ACTION, .data=cJSON_PrintUnformatted(o)}; cJSON_Delete(o);
    if (!w.data || !xQueueSend(s_work, &w, 0)) { free(w.data); return false; } return true;
}
void muse_pocket_speech_test(void)
{
    if (!s_connected) { muse_state_set_caption("Companion is offline"); return; }
    s_generation++; s_done=false; clear_audio();
    cJSON *o=event("speech.test"); cJSON_AddNumberToObject(o,"generation",s_generation);
    work_t w={.kind=SEND_ACTION,.data=cJSON_PrintUnformatted(o)}; cJSON_Delete(o);
    if (!w.data || !xQueueSend(s_work,&w,0)) free(w.data);
}
bool muse_pocket_console(char *line, bool whole)
{
    if (strncmp(line, "pocket.", 7)) return false;
    if (!strncmp(line, "pocket.ask=", 11)) {
        if (!whole || !s_connected || !line[11]) { printf("@pocket.ask offline-or-invalid\n"); return true; }
        char operation[33]; random_id(operation); s_generation++; s_done=false;
        cJSON *o=event("chat"); cJSON_AddStringToObject(o,"text",line+11); cJSON_AddStringToObject(o,"operation_id",operation); cJSON_AddNumberToObject(o,"generation",s_generation);
        work_t w={.kind=SEND_ACTION,.data=cJSON_PrintUnformatted(o)}; cJSON_Delete(o);
        bool ok=w.data && xQueueSend(s_work,&w,0);
        if (!ok) free(w.data);
        printf("@pocket.ask %s\n",ok?"queued":"busy"); return true;
    }
    if (!strcmp(line, "pocket.status")) {
        char first[65]; size_t total = 0, used = 0;
        int queued = s_fs ? notes(first) : -1;
        if (s_fs) esp_spiffs_info("pocket", &total, &used);
        printf("@pocket {\"configured\":%s,\"connected\":%s,\"storage\":%s,\"busy\":%s,\"queued\":%d,\"storage_total\":%u,\"storage_used\":%u,\"internal_free\":%u,\"internal_largest\":%u,\"psram_free\":%u}\n", muse_pocket_enabled()?"true":"false", s_connected?"true":"false", s_fs?"true":"false", muse_pocket_busy()?"true":"false",queued,(unsigned)total,(unsigned)used,(unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),(unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),(unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM)); return true;
    }
    if (!strcmp(line, "pocket.storage_test")) {
        work_t w = {.kind=STORAGE_TEST};
        if (!whole || !muse_pocket_enabled() || !xQueueSend(s_work, &w, 0)) {
            printf("@pocket.storage {\"ok\":false,\"error\":\"unavailable or busy\"}\n");
            fflush(stdout);
        }
        return true;
    }
    if (!strcmp(line, "pocket.audio_status")) {
        printf("@pocket.audio {\"received_frames\":%u,\"played_frames\":%u,\"write_errors\":%u,\"pending_bytes\":%u}\n",
               (unsigned)s_audio_received, (unsigned)s_audio_played, (unsigned)s_audio_errors, (unsigned)s_audio_size);
        fflush(stdout);
        return true;
    }
    const char *key = NULL; char *value = strchr(line, '=');
    if (value) { *value++ = 0; if (!strcmp(line,"pocket.url")) key="url"; else if (!strcmp(line,"pocket.token")) key="token"; else if (!strcmp(line,"pocket.ca")) key="ca"; else if (!strcmp(line,"pocket.ca+")) key="ca+"; }
    esp_err_t err = ESP_ERR_INVALID_ARG;
    if (whole && key && value) {
        size_t len = strlen(value); bool valid = false;
        if (!strcmp(key,"url")) valid = !len || (len < sizeof(s_url) && !strncmp(value,"wss://",6) && !strchr(value,'@') && !strchr(value,'?') && !strchr(value,'#'));
        else if (!strcmp(key,"token")) valid = len < sizeof(s_token) && !strchr(value,'\r') && !strchr(value,'\n');
        else { /* PEM arrives escaped through the line-oriented USB console. */
            char *src=value, *dst=value; while (*src) { if (src[0]=='\\' && src[1]=='n') { *dst++='\n'; src+=2; } else *dst++=*src++; } *dst=0;
            if (!strcmp(key,"ca")) s_ca[0]=0;
            valid = s_ca && strlen(s_ca) + strlen(value) < CA_BYTES;
            if (valid) strcat(s_ca,value);
        }
        nvs_handle_t n;
        if (valid && nvs_open("muse_pocket",NVS_READWRITE,&n)==ESP_OK) {
            err=nvs_set_str(n, key[0]=='c'?"ca":key, key[0]=='c'?s_ca:value);
            if (err==ESP_OK) err=nvs_commit(n);
            nvs_close(n);
        }
        memset(value,0,strlen(value));
    }
    printf("@pocket.config %s\n",err==ESP_OK?"saved; restart to apply":"error"); fflush(stdout); return true;
}
