#include "muse_pocket.h"
#include "muse_pocket_filename.h"
#include "muse_pocket_policy.h"
#include "muse_notebook_recording.h"
#include "muse_adpcm.h"
#include "muse_link.h"
#include "esp_app_desc.h"
#include "muse_audio.h"
#include "muse_cards.h"
#include "muse_ble.h"
#include "muse_input.h"
#include "muse_mem.h"
#include "muse_settings.h"
#include "muse_state.h"
#include "muse_wifi.h"
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
#include "muse_pocket_recording.h"

#define FS "/pocket"
#define NOTE_BYTES (16000 * 2 * 15)
#define NOTE_LIMIT 3
#define AUDIO_BYTES 65536
#define RX_BYTES 32768
typedef struct { int kind; char *data; size_t len; char id[65]; } work_t;
enum { SAVE_NOTE, ACK_NOTE, CACHE_SYNC, SEND_ACTION, STORAGE_TEST, SAVE_MODE, LOCAL_ACTION, ACK_ACTION, FAIL_ACTION, NOTEBOOK_SAVED };
static const char *TAG = "pocket";
static char s_url[257], s_token[129], *s_ca, *s_access, *s_headers;
#define ACCESS_BYTES 2049
#define HEADERS_BYTES (ACCESS_BYTES + 256)
#define CA_BYTES 4097
static esp_websocket_client_handle_t s_ws;
static QueueHandle_t s_work;
static SemaphoreHandle_t s_send, s_audio_lock;
static unsigned char *s_audio, *s_rx;
static size_t s_audio_read, s_audio_size, s_rx_len, s_rx_expected;
static int s_rx_opcode;
static volatile bool s_connected, s_recording, s_inflight, s_ready, s_done, s_fs;
static volatile bool s_restart_ws;
static bool s_ws_started;
static volatile bool s_setup_paused;
static volatile bool s_announce;
static char s_capture_language[3];
static volatile uint32_t s_generation;
static volatile uint32_t s_audio_received, s_audio_played, s_audio_errors;
static volatile int s_queued;
static volatile int64_t s_retry_at;
static char s_note_id[65];
static cJSON *s_reminders, *s_todos, *s_outbox;
static bool s_outbox_valid = true;
static char s_notebook_id[33];
static volatile bool s_notebook_recording, s_notebook_mark;
static bool s_notebook_finish, s_notebook_error;
static unsigned s_notebook_segments;
static bool s_notebook_markers[MUSE_NOTEBOOK_MAX_SEGMENTS];
static int64_t s_notebook_retry;
static bool save_notebook(void);
static void load_notebook(void);
static void notebook_action(const char *action);
static void notebook_card(void);
static void notebook_tick(void);
static volatile int s_pending_actions;
static volatile pocket_profile_t s_profile = POCKET_BALANCED;
static pocket_power_policy_t s_power_policy;
static volatile uint32_t s_next_sync_seconds; /* atomic read/write on the ESP32 */
static volatile bool s_compatible, s_storage_checked;
static int64_t s_action_retry;
static void local_action(const char *id, const char *action);
static void ack_action(const char *operation, bool failed);
static void replay_outbox(void);
static void local_cards(void);
static void load_local(void);
static void send_health(void);
static bool pending_action(const char *id);

static time_t s_server_time;
static int64_t s_synced_at;
static char s_account_summary[257], s_tools_url[321];
static int64_t s_accounts_at;

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
    COPY_FIELD(id); COPY_FIELD(kind); COPY_FIELD(title); COPY_FIELD(body); COPY_FIELD(source); COPY_FIELD(status); COPY_FIELD(handoff);
    #undef COPY_FIELD
    cJSON *progress = cJSON_GetObjectItem(o, "progress");
    card->has_progress = cJSON_IsNumber(progress) && progress->valueint >= 0 && progress->valueint <= 100;
    card->progress = card->has_progress ? progress->valueint : 0;
    for (int i=0; i<3; i++) {
        cJSON *step = cJSON_GetArrayItem(cJSON_GetObjectItem(o,"steps"),i);
        if (cJSON_IsString(step)) snprintf(card->steps[i],sizeof(card->steps[i]),"%s",step->valuestring);
    }
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
    else if (!strcmp(type, "capture.mode")) {
        const char *language = !strcmp(str(o, "mode"), "translate") ? str(o, "language") : "";
        if (muse_recording_language(language)) {
            xSemaphoreTake(s_audio_lock, portMAX_DELAY);
            bool changed = strcmp(s_capture_language, language) != 0;
            snprintf(s_capture_language, sizeof(s_capture_language), "%s", language);
            xSemaphoreGive(s_audio_lock);
            if (changed) {
                work_t w = {.kind=SAVE_MODE, .data=strdup(language)};
                if (!w.data || !xQueueSend(s_work, &w, 0)) free(w.data);
            }
        }
    }
    else if (!strcmp(type, "notebook.saved")) {
        work_t w={.kind=NOTEBOOK_SAVED}; snprintf(w.id,sizeof(w.id),"%s",str(o,"id")); xQueueSend(s_work,&w,0);
    } else if (!strcmp(type,"notebook.error")) {
        s_notebook_error=true; muse_state_set_caption("Notebook saved locally · finish needs review");
    }
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
    } else if (!strcmp(type, "compatibility.result")) {
        cJSON *features = cJSON_GetObjectItem(o, "features"), *f;
        bool receipts = false, notebooks = false;
        cJSON_ArrayForEach(f, features) if (cJSON_IsString(f)) {
            if (!strcmp(f->valuestring,"action_receipts_v1")) receipts=true;
            if (!strcmp(f->valuestring,"notebook_segments_v1")) notebooks=true;
        }
        cJSON *protocol = cJSON_GetObjectItem(o, "protocol"), *schema = cJSON_GetObjectItem(o, "storage_schema");
        s_compatible = protocol && schema && pocket_compatible(protocol->valueint, schema->valueint,
            cJSON_IsTrue(cJSON_GetObjectItem(o, "storage_ready")), receipts && notebooks,
            heap_caps_get_free_size(MALLOC_CAP_INTERNAL), heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL), s_storage_checked);
    } else if (!strcmp(type, "action.error")) {
        work_t w = {.kind=FAIL_ACTION}; snprintf(w.id, sizeof(w.id), "%s", str(o, "operation_id"));
        xQueueSend(s_work, &w, 0);
        muse_state_set_caption("Action needs review in companion");
    } else if (!strcmp(type, "action.result")) {
        work_t w = {.kind=ACK_ACTION}; snprintf(w.id, sizeof(w.id), "%s", str(o, "operation_id"));
        xQueueSend(s_work, &w, 0);
        cJSON *result = cJSON_GetObjectItemCaseSensitive(o, "result");
        cJSON *card = cJSON_GetObjectItemCaseSensitive(result, "card");
        if (!cJSON_IsObject(card) || strcmp(str(card, "id"), str(o, "id"))) muse_cards_remove(str(o, "id"));
        if (cJSON_IsObject(card)) card_from_json(card);
        muse_state_set_caption("Action confirmed");
    } else if (!strcmp(type, "error")) {
        muse_state_set_caption("%s", str(o, "text"));
        muse_state_set_mode(MUSE_MODE_ERROR);
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
    if (id == WEBSOCKET_EVENT_CONNECTED) { s_connected = true; s_announce = true; s_retry_at = 0; ESP_LOGI(TAG, "companion connected"); }
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

/* The WebSocket owns an 8 KiB internal stack. The phone's provisioning worker
 * needs another 8 KiB, which cannot be allocated beside BLE on this board.
 * Stop the network task during phone setup; keep the storage worker and its
 * durable captures alive. Only this worker starts/stops the WebSocket. */
static void service_connection(void)
{
    if (!s_ws) return;
    muse_ble_status_t ble;
    muse_ble_status(&ble);
    bool setup = ble.state == MUSE_BLE_CONNECTED;
    bool allowed = !setup && muse_wifi_connected();
    if (setup != s_setup_paused) {
        s_setup_paused = setup;
        ESP_LOGI(TAG, "%s", setup ? "pausing companion for phone setup" : "phone setup ended; companion may resume");
    }
    if (!allowed) {
        if (s_ws_started) {
            esp_websocket_client_stop(s_ws);
            s_ws_started = false;
            s_connected = false; s_ready = false; s_inflight = false; s_done = true;
            s_rx_len = 0;
        }
        s_restart_ws = false;
        return;
    }
    if (s_restart_ws) {
        /* A graceful server close ends the IDF task; transport failures retry
         * inside it. Stop also waits for the previous task to finish. */
        esp_websocket_client_stop(s_ws);
        /* CLOSED is emitted just before the IDF task finishes cleanup. Its
         * run flag may already be false, so stop can return without waiting. */
        vTaskDelay(pdMS_TO_TICKS(1000));
        s_ws_started = false;
        s_restart_ws = false;
    }
    if (!s_ws_started)
        s_ws_started = esp_websocket_client_start(s_ws) == ESP_OK;
}
static void load_nvs(void)
{
    nvs_handle_t n; if (nvs_open("muse_pocket", NVS_READONLY, &n) != ESP_OK) return;
    size_t len = sizeof(s_url); nvs_get_str(n, "url", s_url, &len);
    len = sizeof(s_token); nvs_get_str(n, "token", s_token, &len);
    len = CA_BYTES; nvs_get_str(n, "ca", s_ca, &len);
    len = ACCESS_BYTES; nvs_get_str(n, "access", s_access, &len);
    uint8_t profile = POCKET_BALANCED; nvs_get_u8(n, "profile", &profile);
    s_profile = profile <= POCKET_TRAVEL ? profile : POCKET_BALANCED;
    len = sizeof(s_capture_language); nvs_get_str(n, "interp_lang", s_capture_language, &len);
    if (!muse_recording_language(s_capture_language)) s_capture_language[0] = 0;
    nvs_close(n);
}
void muse_pocket_init(void)
{
    s_ca = heap_caps_calloc(1, CA_BYTES, MUSE_BIG_CAPS);
    s_access = heap_caps_calloc(1, ACCESS_BYTES, MUSE_BIG_CAPS);
    s_headers = heap_caps_calloc(1, HEADERS_BYTES, MUSE_BIG_CAPS);
    if (!s_ca || !s_access || !s_headers) return;
    load_nvs(); muse_cards_init();
    s_audio = heap_caps_malloc(AUDIO_BYTES, MUSE_BIG_CAPS); s_rx = heap_caps_malloc(RX_BYTES, MUSE_BIG_CAPS);
    s_work = xQueueCreate(10, sizeof(work_t)); s_send = xSemaphoreCreateMutex(); s_audio_lock = xSemaphoreCreateMutex();
    if (!s_audio || !s_rx || !s_work || !s_send || !s_audio_lock) { s_url[0] = 0; ESP_LOGE(TAG, "out of memory"); }
}
bool muse_pocket_enabled(void) { return !strncmp(s_url, "wss://", 6) && s_token[0]; }
bool muse_pocket_connected(void) { return s_connected; }
int muse_pocket_queued(void) { return s_queued; }
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
    bool notebook=s_notebook_id[0] && !s_notebook_finish;
    if (!s_fs || s_queued >= (notebook?16:NOTE_LIMIT)) { muse_state_set_caption("Capture queue full or unavailable · reconnect to sync"); return; }
    s_recording=true; s_generation++; s_inflight=false; s_done=false; clear_audio();
    if (s_connected) send_json(event("voice.cancel"));
    if (notebook) s_notebook_recording=true;
    bool stop=false;
    do {
        size_t header_size=notebook?MUSE_NOTEBOOK_HEADER_SIZE:MUSE_RECORDING_HEADER_SIZE;
        unsigned char *buffer=heap_caps_malloc(NOTE_BYTES+header_size,MUSE_BIG_CAPS);
        if (!buffer) { muse_state_set_caption("Not enough memory to record"); break; }
        unsigned char *pcm=buffer+header_size; size_t used=0;
        char language[3];
        xSemaphoreTake(s_audio_lock,portMAX_DELAY); memcpy(language,s_capture_language,sizeof(language)); xSemaphoreGive(s_audio_lock);
        muse_state_set_mode(MUSE_MODE_LISTENING);
        muse_state_set_caption("%s",notebook?"Recording notebook · Pause or release to stop":language[0]?"Translating · release to hear":"Listening · release to save");
        while (used+MUSE_AUDIO_CHUNK*2<=NOTE_BYTES) {
            muse_input_event_t e;
            if ((xQueueReceive(input,&e,0) && e.type==MUSE_PTT_UP) || (notebook && !s_notebook_recording)) { stop=true; break; }
            if (muse_audio_read((int16_t *)(pcm+used),MUSE_AUDIO_CHUNK)!=ESP_OK) { stop=true; break; }
            muse_state_set_level(muse_audio_level((int16_t *)(pcm+used),MUSE_AUDIO_CHUNK));
            used+=MUSE_AUDIO_CHUNK*2; muse_state_set_progress((float)used/NOTE_BYTES);
        }
        if (used>=8000) {
            bool ok;
            if (notebook) {
                muse_notebook_segment_t segment={.pcm_bytes=used,.marked=s_notebook_mark};
                snprintf(segment.id,sizeof(segment.id),"%s",s_notebook_id); s_notebook_mark=false;
                ok=muse_notebook_encode(buffer,&segment);
                muse_adpcm_t codec={0}; muse_adpcm_encode_block(&codec,(int16_t *)pcm,used/2,pcm);
                used/=4;
            } else ok=muse_recording_encode(buffer,language,used);
            work_t w={.kind=SAVE_NOTE,.data=(char *)buffer,.len=used+header_size}; random_id(w.id);
            if (!ok || !xQueueSend(s_work,&w,0)) { free(buffer); muse_state_set_caption("Capture NOT saved · queue busy"); stop=true; }
        } else free(buffer);
        if (notebook && (s_queued>=16 || s_notebook_segments>=MUSE_NOTEBOOK_MAX_SEGMENTS || s_notebook_error)) {
            muse_state_set_caption("Notebook paused · sync saved segments before resuming"); stop=true;
        }
    } while (notebook && !stop && s_notebook_recording);
    s_notebook_recording=false; s_recording=false; muse_state_set_level(0); muse_state_set_progress(0); muse_state_set_mode(MUSE_MODE_IDLE);
    if (notebook) muse_state_set_caption("Notebook paused · saved segments can sync");
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
/* SPIFFS rename refuses an existing destination. Keep the last committed
 * version under .bak while installing its replacement, and recover it after
 * an interrupted replacement. A .tmp file alone is never considered saved. */
static bool recover_storage_file(const char *name)
{
    struct stat st;
    if (!stat(name, &st)) return true;
    if (errno != ENOENT) return false;
    char backup[100];
    if ((size_t)snprintf(backup, sizeof(backup), "%s.bak", name) >= sizeof(backup)) { errno=ENAMETOOLONG; return false; }
    if (!stat(backup, &st)) return rename(backup, name) == 0;
    return errno == ENOENT;
}
static bool storage_file_missing(const char *name)
{
    struct stat st;
    if (!stat(name, &st) || errno != ENOENT) return false;
    char backup[100];
    if ((size_t)snprintf(backup, sizeof(backup), "%s.bak", name) >= sizeof(backup)) return false;
    return stat(backup, &st) != 0 && errno == ENOENT;
}
static bool remove_storage_file(const char *name)
{
    char backup[100];
    if ((size_t)snprintf(backup, sizeof(backup), "%s.bak", name) >= sizeof(backup)) { errno=ENAMETOOLONG; return false; }
    /* Remove old versions first so a later read cannot resurrect deleted data. */
    if (unlink(backup) && errno != ENOENT) return false;
    return unlink(name) == 0;
}
static bool write_atomic(const char *name, const void *data, size_t len)
{
    char temp[100], backup[100];
    if ((size_t)snprintf(temp, sizeof(temp), "%s.tmp", name) >= sizeof(temp) ||
        (size_t)snprintf(backup, sizeof(backup), "%s.bak", name) >= sizeof(backup)) { errno=ENAMETOOLONG; return false; }
    if (!recover_storage_file(name)) return false;
    FILE *f = fopen(temp, "wb");
    if (!f) { ESP_LOGE(TAG, "open storage file failed: errno %d (%s)", errno, strerror(errno)); return false; }
    bool ok = fwrite(data, 1, len, f) == len && fflush(f) == 0;
    int error = ok ? 0 : errno;
    if (ok) ok = fsync(fileno(f)) == 0;
    if (!ok && !error) error = errno;
    if (fclose(f)) { if (!error) error = errno; ok = false; }
    bool moved = false;
    if (ok) {
        struct stat st;
        if (!stat(name, &st)) {
            ok = !unlink(backup) || errno == ENOENT;
            if (ok) { moved = rename(name, backup) == 0; ok = moved; }
        } else if (errno != ENOENT) ok = false;
    }
    if (ok) ok = rename(temp, name) == 0;
    if (ok && moved) (void)unlink(backup);
    if (!ok) {
        if (!error) error = errno;
        if (moved) (void)recover_storage_file(name);
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
    /* Replacement matters too: creating a new file alone misses SPIFFS's
     * refusal to rename over an existing destination. */
    if (ok) { expected[0] ^= 0xff; ok = write_atomic(path, expected, sizeof(expected)); }
    if (ok) {
        FILE *f = fopen(path, "rb");
        ok = f && fread(actual, 1, sizeof(actual), f) == sizeof(actual) && !memcmp(actual, expected, sizeof(actual));
        if (f && fclose(f)) ok = false;
    }
    if (s_fs && !remove_storage_file(path) && errno != ENOENT) ok = false;
    size_t total = 0, used = 0;
    esp_spiffs_info("pocket", &total, &used);
    printf("@pocket.storage {\"ok\":%s,\"total\":%u,\"used\":%u}\n",
           ok ? "true" : "false", (unsigned)total, (unsigned)used);
    fflush(stdout);
    s_storage_checked = ok;
    muse_state_set_caption("%s", ok ? "Storage ready - hold to talk" : "Storage check failed");
}
static void update_cache(const char *data, bool persist)
{
    cJSON *o = cJSON_Parse(data); if (!o) return;
    cJSON *accounts = cJSON_GetObjectItem(o, "accounts");
    const char *summary = str(accounts, "summary"), *tools_url = str(accounts, "tools_url");
    bool changed = !cJSON_Compare(s_reminders, cJSON_GetObjectItem(o, "reminders"), true) ||
                   !cJSON_Compare(s_todos, cJSON_GetObjectItem(o, "todos"), true) ||
                   strcmp(s_account_summary, summary) || strcmp(s_tools_url, tools_url);
    snprintf(s_account_summary, sizeof(s_account_summary), "%s", summary);
    snprintf(s_tools_url, sizeof(s_tools_url), "%s", !strncmp(tools_url, "https://", 8) ? tools_url : "");
    if (persist && summary[0]) s_accounts_at = esp_timer_get_time();
    cJSON_Delete(s_todos); s_todos = cJSON_Duplicate(cJSON_GetObjectItem(o, "todos"), true);
    cJSON_Delete(s_reminders); s_reminders = cJSON_Duplicate(cJSON_GetObjectItem(o, "reminders"), true);
    cJSON *t = cJSON_GetObjectItem(o, "server_epoch");
    if (persist && cJSON_IsNumber(t)) { s_server_time = (time_t)t->valuedouble; s_synced_at = esp_timer_get_time(); }
    if (persist && changed && s_fs) write_atomic(FS "/reminders.json", data, strlen(data));
    cJSON_Delete(o);
    local_cards();
}
static void check_reminders(void)
{
    time_t now = s_server_time ? s_server_time + (esp_timer_get_time() - s_synced_at) / 1000000 : time(NULL);
    if (now < 1760000000) return; /* Clock unknown after an offline cold boot: do not invent a due time. */
    cJSON *r;
    cJSON_ArrayForEach(r, s_reminders) {
        if (pending_action(str(r, "id"))) continue;
        cJSON *due = cJSON_GetObjectItem(r, "due");
        if (!cJSON_IsNumber(due) || due->valuedouble > now || strcmp(str(r, "state"), "scheduled") || str(r, "ssid")[0]) continue;
        cJSON_ReplaceItemInObject(r, "state", cJSON_CreateString("due"));
        muse_card_t *card = heap_caps_calloc(1, sizeof(*card), MUSE_BIG_CAPS); if (!card) continue;
        snprintf(card->id, sizeof(card->id), "%s", str(r, "id")); snprintf(card->kind, sizeof(card->kind), "reminder");
        snprintf(card->title, sizeof(card->title), "%s", str(r, "title")); snprintf(card->body, sizeof(card->body), "Saved reminder. Done and Snooze can wait for a connection.");
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
    uint8_t header[MUSE_NOTEBOOK_HEADER_SIZE]; char language[3]={0};
    muse_notebook_segment_t segment;
    fseek(f, 0, SEEK_END); long file_size = ftell(f); rewind(f);
    size_t read = fread(header, 1, sizeof(header), f);
    int notebook = muse_notebook_decode(header,read,file_size>0?(size_t)file_size:0,&segment);
    int format = notebook ? notebook : muse_recording_decode(header, read, file_size > 0 ? (size_t)file_size : 0, language);
    if (format < 0 || file_size <= 0) {
        fclose(f); muse_state_set_caption("Saved capture damaged - not sent");
        s_retry_at = esp_timer_get_time() + 300000000; return;
    }
    fseek(f,notebook>0?MUSE_NOTEBOOK_HEADER_SIZE:format?MUSE_RECORDING_HEADER_SIZE:0,SEEK_SET);
    s_generation++; s_ready = false; s_done = false; s_inflight = true;
    snprintf(s_note_id, sizeof(s_note_id), "%s", id);
    uint32_t epoch = s_generation;
    cJSON *o = event("voice.begin"); cJSON_AddStringToObject(o, "id", id); cJSON_AddStringToObject(o, "mode", notebook>0?"notebook":language[0] ? "translate" : "recorded"); cJSON_AddStringToObject(o, "language", language); cJSON_AddNumberToObject(o, "generation", epoch);
    if (notebook>0) {
        cJSON_AddStringToObject(o,"notebook",segment.id); cJSON_AddNumberToObject(o,"position",segment.position); cJSON_AddBoolToObject(o,"marked",segment.marked);
    }
    bool ok = send_json(o);
    for (int i = 0; ok && !s_ready && s_connected && (!s_recording || s_notebook_recording) && i < 100; ++i) vTaskDelay(pdMS_TO_TICKS(20));
    ok = ok && s_ready;
    _Alignas(4) unsigned char packet[1284]; unsigned char compressed[320]; memcpy(packet, &epoch, 4); size_t n;
    muse_adpcm_t codec={0};
    while (ok && s_connected && (!s_recording || s_notebook_recording) && epoch == s_generation &&
           (n = fread(notebook>0?compressed:packet+4,1,notebook>0?sizeof(compressed):sizeof(packet)-4,f))) {
        if (notebook>0) { muse_adpcm_decode_block(&codec,compressed,n*2,(int16_t *)(packet+4)); n*=4; }
        if (xSemaphoreTake(s_send, pdMS_TO_TICKS(2000))) {
            ok = esp_websocket_client_send_bin(s_ws, (char *)packet, n + 4, pdMS_TO_TICKS(2000)) == (int)n + 4;
            xSemaphoreGive(s_send);
        } else ok = false;
        vTaskDelay(1);
    }
    fclose(f);
    if (ok && epoch == s_generation && (!s_recording || s_notebook_recording)) {
        ok = send_json(event("voice.end"));
        if (ok && notebook<=0) { muse_state_set_mode(MUSE_MODE_THINKING); muse_state_set_caption("Working on your capture..."); }
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
    if (!s_fs) {
        ESP_LOGE(TAG, "pocket partition unavailable: captures cannot be saved");
        muse_state_set_caption("Storage unavailable - captures cannot be saved");
        muse_state_set_mode(MUSE_MODE_ERROR);
    }
    char first_note[65];
    s_queued = s_fs ? notes(first_note) : -1;
    FILE *cache = s_fs && recover_storage_file(FS "/reminders.json") ? fopen(FS "/reminders.json", "rb") : NULL;
    if (cache) {
        char *data = heap_caps_calloc(1, 8192, MUSE_BIG_CAPS);
        if (data) { fread(data, 1, 8191, cache); update_cache(data, false); free(data); } fclose(cache);
    }
    load_local();
    load_notebook();
    storage_test();
    if (s_access[0]) snprintf(s_headers, HEADERS_BYTES, "Authorization: Bearer %s\r\nX-Muse-Authorization: Bearer %s\r\n", s_access, s_token);
    else snprintf(s_headers, HEADERS_BYTES, "Authorization: Bearer %s\r\n", s_token);
    esp_websocket_client_config_t config = {.uri=s_url, .headers=s_headers, .task_stack=8192, .buffer_size=2048, .network_timeout_ms=5000, .reconnect_timeout_ms=10000, .ping_interval_sec=15};
    if (s_ca[0]) config.cert_pem = s_ca; else config.crt_bundle_attach = esp_crt_bundle_attach;
    s_ws = esp_websocket_client_init(&config);
    if (s_ws) esp_websocket_register_events(s_ws, WEBSOCKET_EVENT_ANY, ws_event, NULL);
    int64_t ping_at = 0;
    for (;;) {
        service_connection();
        work_t w;
        if (xQueueReceive(s_work, &w, pdMS_TO_TICKS(1000))) {
            char path[100], first[65];
            if (w.kind == SAVE_NOTE) {
                int queued = s_fs ? notes(first) : -1;
                muse_notebook_segment_t segment;
                int notebook=muse_notebook_decode((uint8_t *)w.data,w.len,w.len,&segment);
                bool ok = queued >= 0 && queued < (notebook>0?16:NOTE_LIMIT);
                if (notebook>0) {
                    ok=ok && !strcmp(segment.id,s_notebook_id) && s_notebook_segments<MUSE_NOTEBOOK_MAX_SEGMENTS;
                    segment.position=s_notebook_segments;
                    ok=ok && muse_notebook_encode((uint8_t *)w.data,&segment);
                }
                nvs_handle_t n;
                if (ok && nvs_open("muse_pocket",NVS_READWRITE,&n)==ESP_OK) {
                    uint64_t seq=0; nvs_get_u64(n,"note_seq",&seq); seq++;
                    ok = nvs_set_u64(n,"note_seq",seq)==ESP_OK && nvs_commit(n)==ESP_OK;
                    nvs_close(n);
                    char prefix[17]; snprintf(prefix,sizeof(prefix),"%016llx",(unsigned long long)seq); memcpy(w.id,prefix,16);
                    ok = ok && note_path(w.id, path);
                } else ok=false;
                ok = ok && write_atomic(path, w.data, w.len);
                s_queued = queued + (ok ? 1 : 0);
                if (notebook>0) {
                    if (ok) {
                        s_notebook_markers[segment.position]=segment.marked; s_notebook_segments++;
                        if (!save_notebook()) { s_notebook_error=true; s_notebook_recording=false; }
                    } else { s_notebook_error=true; s_notebook_recording=false; }
                }
                ESP_LOGI(TAG, "capture save: %s (%u bytes, %d already queued)", ok ? "ok" : "failed", (unsigned)w.len, queued);
                const char *message = ok ? "Capture saved · waiting for companion" :
                    queued < 0 ? "Storage unavailable - capture NOT saved" :
                    queued >= NOTE_LIMIT ? "Queue full - new capture NOT saved" : "Save failed - capture NOT saved";
                muse_state_set_caption("%s", message);
                if (!ok) muse_state_set_mode(MUSE_MODE_ERROR);
            } else if (w.kind == ACK_NOTE && note_path(w.id, path)) {
                if (remove_storage_file(path)) {
                    if (s_queued > 0) s_queued--;
                    s_retry_at=0;
                    ESP_LOGI(TAG, "capture acknowledged and removed");
                }
            }
            else if (w.kind == CACHE_SYNC) update_cache(w.data, true);
            else if (w.kind == SEND_ACTION) { cJSON *o = cJSON_Parse(w.data); if (o && !send_json(o)) muse_state_set_caption("Action not sent · try again when connected"); }
            else if (w.kind == STORAGE_TEST) storage_test();
            else if (w.kind == LOCAL_ACTION) local_action(w.id, w.data);
            else if (w.kind == NOTEBOOK_SAVED && !strcmp(w.id,s_notebook_id) && s_notebook_finish) {
                if (remove_storage_file(FS "/notebook.json")) {
                    s_notebook_id[0]=0; s_notebook_segments=0; s_notebook_finish=false; s_notebook_error=false;
                    memset(s_notebook_markers,0,sizeof(s_notebook_markers));
                    muse_state_set_caption("Notebook confirmed · summary will appear in Inbox");
                }
            }
            else if (w.kind == ACK_ACTION || w.kind == FAIL_ACTION) ack_action(w.id, w.kind == FAIL_ACTION);
            else if (w.kind == SAVE_MODE) {
                nvs_handle_t n;
                if (nvs_open("muse_pocket", NVS_READWRITE, &n) == ESP_OK) {
                    nvs_set_str(n, "interp_lang", w.data); nvs_commit(n); nvs_close(n);
                }
            }
            free(w.data);
        }
        if (s_connected && s_announce) {
            cJSON *hello = event("device.hello"), *caps = cJSON_AddArrayToObject(hello, "capabilities");
            cJSON_AddItemToArray(caps, cJSON_CreateString("capture_modes_v1"));
            cJSON_AddItemToArray(caps, cJSON_CreateString("action_outbox_v1"));
            send_json(event("compatibility.check"));
            send_health();
            if (send_json(hello)) s_announce = false;
        }
        check_reminders();
        replay_outbox();
        notebook_tick();
        muse_mode_t mode = muse_state_mode(NULL);
        if (s_done && !s_audio_size && !s_recording && (mode == MUSE_MODE_SPEAKING || mode == MUSE_MODE_THINKING)) muse_state_set_mode(MUSE_MODE_IDLE);
        if (!s_connected) continue;
        int64_t now = esp_timer_get_time();
        if (now > ping_at) {
            send_json(event("ping")); send_health(); send_json(event("compatibility.check")); wifi_ap_record_t ap;
            if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) { cJSON *o = event("network"); cJSON_AddStringToObject(o, "ssid", (char *)ap.ssid); send_json(o); }
            ping_at = now + 15000000;
        }
        if ((!s_recording || s_notebook_recording) && !s_inflight && !s_audio_size && now > s_retry_at && s_fs && !s_notebook_error) { char id[65]; if (notes(id) > 0) upload_note(id); }
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
    if (!strncmp(id, "local.", 6) || !strcmp(action, "done") || !strcmp(action, "snooze") || !strcmp(action,"retry_saved") || !strcmp(action,"forget_saved")) {
        work_t w = {.kind=LOCAL_ACTION, .data=strdup(action)};
        snprintf(w.id, sizeof(w.id), "%s", id);
        if (!w.data || !s_work || !xQueueSend(s_work, &w, 0)) { free(w.data); return false; }
        return true;
    }
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
        char language[3] = {0};
        if (s_audio_lock) {
            xSemaphoreTake(s_audio_lock, portMAX_DELAY); memcpy(language, s_capture_language, sizeof(language)); xSemaphoreGive(s_audio_lock);
        }
        printf("@pocket {\"configured\":%s,\"connected\":%s,\"storage\":%s,\"busy\":%s,\"queued\":%d,\"storage_total\":%u,\"storage_used\":%u,\"capture_mode\":\"%s\",\"capture_language\":\"%s\",\"recording_format\":2,\"internal_free\":%u,\"internal_largest\":%u,\"psram_free\":%u}\n", muse_pocket_enabled()?"true":"false", s_connected?"true":"false", s_fs?"true":"false", muse_pocket_busy()?"true":"false",queued,(unsigned)total,(unsigned)used,language[0]?"translate":"recorded",language,(unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),(unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),(unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM)); return true;
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
    if (value) { *value++ = 0; if (!strcmp(line,"pocket.url")) key="url"; else if (!strcmp(line,"pocket.token")) key="token"; else if (!strcmp(line,"pocket.ca")) key="ca"; else if (!strcmp(line,"pocket.ca+")) key="ca+"; else if (!strcmp(line,"pocket.access")) key="access"; else if (!strcmp(line,"pocket.access+")) key="access+"; }
    esp_err_t err = ESP_ERR_INVALID_ARG;
    if (whole && key && value) {
        size_t len = strlen(value); bool valid = false;
        if (!strcmp(key,"url")) valid = !len || (len < sizeof(s_url) && !strncmp(value,"wss://",6) && !strchr(value,'@') && !strchr(value,'?') && !strchr(value,'#'));
        else if (!strcmp(key,"token")) valid = len < sizeof(s_token) && !strchr(value,'\r') && !strchr(value,'\n');
        else if (!strncmp(key,"access",6)) {
            if (!strcmp(key,"access")) s_access[0]=0;
            valid = s_access && strlen(s_access) + len < ACCESS_BYTES;
            for (size_t i=0; valid && i<len; ++i) valid = (unsigned char)value[i] >= 33 && (unsigned char)value[i] <= 126;
            if (valid) strcat(s_access,value);
        }
        else { /* PEM arrives escaped through the line-oriented USB console. */
            char *src=value, *dst=value; while (*src) { if (src[0]=='\\' && src[1]=='n') { *dst++='\n'; src+=2; } else *dst++=*src++; } *dst=0;
            if (!strcmp(key,"ca")) s_ca[0]=0;
            valid = s_ca && strlen(s_ca) + strlen(value) < CA_BYTES;
            if (valid) strcat(s_ca,value);
        }
        nvs_handle_t n;
        if (valid && nvs_open("muse_pocket",NVS_READWRITE,&n)==ESP_OK) {
            err=nvs_set_str(n, key[0]=='c'?"ca":key[0]=='a'?"access":key, key[0]=='c'?s_ca:key[0]=='a'?s_access:value);
            if (err==ESP_OK) err=nvs_commit(n);
            nvs_close(n);
        }
        memset(value,0,strlen(value));
    }
    printf("@pocket.config %s\n",err==ESP_OK?"saved; restart to apply":"error"); fflush(stdout); return true;
}

/* The storage worker is the sole owner of the local register and outbox. */
static time_t pocket_now(void)
{
    time_t now = s_server_time ? s_server_time + (esp_timer_get_time() - s_synced_at) / 1000000 : time(NULL);
    return now >= 1760000000 ? now : 0;
}
static cJSON *read_json_file(const char *path, size_t limit)
{
    if (!recover_storage_file(path)) return NULL;
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    char *text = heap_caps_calloc(1, limit + 1, MUSE_BIG_CAPS);
    cJSON *value = NULL;
    if (text) {
        size_t n = fread(text, 1, limit, f);
        if (n < limit && !ferror(f)) value = cJSON_Parse(text);
        free(text);
    }
    fclose(f); return value;
}
static bool persist_json(const char *path, cJSON *value)
{
    char *data = cJSON_PrintUnformatted(value);
    bool ok = data && s_fs && write_atomic(path, data, strlen(data));
    free(data); return ok;
}
static bool pending_action(const char *id)
{
    cJSON *a, *last=NULL;
    cJSON_ArrayForEach(a, s_outbox) if (!strcmp(str(a,"id"), id)) last=a;
    if (!last) return false;
    cJSON *until=cJSON_GetObjectItem(last,"snooze_until");
    return cJSON_IsTrue(cJSON_GetObjectItem(last,"review")) || strcmp(str(last,"action"),"snooze") ||
           !cJSON_IsNumber(until) || !pocket_now() || until->valuedouble>pocket_now();
}
static int64_t s_timer_end_ms;
static unsigned s_timer_remaining;
static bool s_timer_due;
static bool save_timer(unsigned remaining, int64_t end, bool due)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddNumberToObject(o, "remaining", remaining);
    cJSON_AddNumberToObject(o, "due_at", end && pocket_now() ? pocket_now() + remaining : 0);
    cJSON_AddBoolToObject(o, "running", end != 0); cJSON_AddBoolToObject(o, "due", due);
    bool ok = persist_json(FS "/timer.json", o); cJSON_Delete(o);
    if (ok) { s_timer_end_ms = end; s_timer_remaining = remaining; s_timer_due = due; }
    return ok;
}
static void load_local(void)
{
    s_outbox = read_json_file(FS "/outbox.json", 8192);
    if (!s_outbox) {
        s_outbox_valid = storage_file_missing(FS "/outbox.json");
        s_outbox = cJSON_CreateArray();
    }
    if (!cJSON_IsArray(s_outbox) || cJSON_GetArraySize(s_outbox) > 16) s_outbox_valid = false;
    cJSON *a;
    cJSON_ArrayForEach(a, s_outbox) {
        if (!safe_id(str(a,"operation_id")) || !str(a,"id")[0] || strlen(str(a,"id")) > 64 ||
            (strcmp(str(a,"action"),"done") && strcmp(str(a,"action"),"snooze"))) s_outbox_valid = false;
    }
    s_pending_actions = s_outbox_valid ? cJSON_GetArraySize(s_outbox) : 0;
    cJSON *timer = read_json_file(FS "/timer.json", 512);
    if (timer) {
        cJSON *remaining = cJSON_GetObjectItem(timer,"remaining"), *due = cJSON_GetObjectItem(timer,"due_at");
        if (cJSON_IsNumber(remaining) && remaining->valuedouble >= 0 && remaining->valuedouble <= 86400) {
            s_timer_remaining = remaining->valueint;
            s_timer_due = cJSON_IsTrue(cJSON_GetObjectItem(timer,"due"));
            if (cJSON_IsTrue(cJSON_GetObjectItem(timer,"running")) && cJSON_IsNumber(due) && due->valuedouble > 0 && pocket_now()) {
                s_timer_remaining = due->valuedouble > pocket_now() ? (unsigned)(due->valuedouble - pocket_now()) : 0;
                s_timer_due = !s_timer_remaining;
                s_timer_end_ms = s_timer_remaining ? esp_timer_get_time()/1000 + s_timer_remaining*1000LL : 0;
            }
            /* Unknown cold-boot clock: leave paused for explicit resume. */
        }
        cJSON_Delete(timer);
    }
    local_cards();
}
static void local_cards(void)
{
    muse_card_t *c = heap_caps_calloc(1, sizeof(*c), MUSE_BIG_CAPS);
    if (!c) return;
    cJSON *todo;
    cJSON_ArrayForEach(todo, s_todos) {
        memset(c, 0, sizeof(*c));
        snprintf(c->id,sizeof(c->id),"%s",str(todo,"id")); snprintf(c->kind,sizeof(c->kind),"task");
        snprintf(c->title,sizeof(c->title),"%s",str(todo,"title"));
        snprintf(c->body,sizeof(c->body),"Saved checklist item. Done will be confirmed when the companion reconnects.");
        snprintf(c->source,sizeof(c->source),"Saved on device");
        snprintf(c->buttons[0].label,25,"Done"); snprintf(c->buttons[0].action,16,"done"); muse_cards_submit(c);
    }
    cJSON *a;
    if (s_outbox_valid) cJSON_ArrayForEach(a, s_outbox) {
        memset(c,0,sizeof(*c));
        snprintf(c->id,sizeof(c->id),"%s",str(a,"id")); snprintf(c->title,sizeof(c->title),"%s",str(a,"title"));
        bool review = cJSON_IsTrue(cJSON_GetObjectItem(a,"review"));
        bool elapsed=!review && !strcmp(str(a,"action"),"snooze") && !pending_action(str(a,"id"));
        snprintf(c->body,sizeof(c->body),"%s saved locally. %s", !strcmp(str(a,"action"),"done")?"Done":"Snooze",
                 review?"Companion could not confirm this action. Retry reuses the saved receipt ID. Forget removes only this local change.":elapsed?"The snooze time has ended. The companion has not confirmed synchronization.":"Waiting for the companion to confirm.");
        if (review) {
            snprintf(c->buttons[0].label,25,"Retry"); snprintf(c->buttons[0].action,16,"retry_saved");
            snprintf(c->buttons[1].label,25,"Forget"); snprintf(c->buttons[1].action,16,"forget_saved");
        } else if (elapsed) {
            snprintf(c->buttons[0].label,25,"Done"); snprintf(c->buttons[0].action,16,"done");
            snprintf(c->buttons[1].label,25,"Snooze 10m"); snprintf(c->buttons[1].action,16,"snooze");
        }
        snprintf(c->source,sizeof(c->source),"%s",review?"Needs review":elapsed?"Reminder due · pending sync":"Saved locally · pending sync"); muse_cards_submit(c);
    }
    memset(c,0,sizeof(*c)); snprintf(c->id,sizeof(c->id),"local.power"); snprintf(c->title,sizeof(c->title),"Connection & power");
    bool accounts_fresh = s_connected && s_accounts_at && esp_timer_get_time() - s_accounts_at < 45000000;
    snprintf(c->body,sizeof(c->body),"Wi-Fi: %s\nCompanion: %s\nMuse account: %s\nPending actions: %d\n\n%s\n%s\n\nProfile: %s\n%s\n%s",
             muse_wifi_connected()?"connected":"offline",s_connected?"connected":"offline",muse_link_hatch_linked()?"paired":"not paired",s_pending_actions,
             accounts_fresh?"Work accounts:":"Work accounts (not live):",s_account_summary[0]?s_account_summary:"Reconnect the companion to check access.",pocket_profile_name(s_profile),
             s_profile==POCKET_RESPONSIVE?"Stays connected while asleep.":s_profile==POCKET_TRAVEL?"30-minute sync cycle after 30 seconds asleep.":"5-minute sync cycle after 2 minutes asleep.",
             s_outbox_valid?"USB or a button wakes connectivity.":"Saved actions need storage recovery.");
    snprintf(c->handoff,sizeof(c->handoff),"%s",s_tools_url);
    snprintf(c->source,sizeof(c->source),"%s",s_tools_url[0]?"Phone opens account recovery in Tools":"Reconnect to check work accounts");
    if (s_next_sync_seconds > esp_timer_get_time()/1000000) {
        size_t len = strlen(c->body);
        snprintf(c->body+len,sizeof(c->body)-len,"\nNext sync in about %lld min.",(long long)((s_next_sync_seconds-esp_timer_get_time()/1000000+59)/60));
    }
    snprintf(c->buttons[0].label,25,"Responsive"); snprintf(c->buttons[0].action,16,"responsive");
    snprintf(c->buttons[1].label,25,"Balanced"); snprintf(c->buttons[1].action,16,"balanced");
    snprintf(c->buttons[2].label,25,"Travel"); snprintf(c->buttons[2].action,16,"travel"); muse_cards_submit(c);
    memset(c,0,sizeof(*c)); snprintf(c->id,sizeof(c->id),"local.timer"); snprintf(c->title,sizeof(c->title),"Focus timer");
    unsigned seconds = s_timer_end_ms > 0 ? (unsigned)((s_timer_end_ms > esp_timer_get_time()/1000 ? s_timer_end_ms-esp_timer_get_time()/1000 : 0)+999)/1000 : s_timer_remaining;
    snprintf(c->body,sizeof(c->body),"%s\n%u:%02u\nRuns on this device without Wi-Fi. After a restart with no clock, resume the saved time.",s_timer_due?"Time is up":s_timer_end_ms?"Running":seconds?"Paused":"Choose a duration", seconds/60,seconds%60);
    snprintf(c->source,sizeof(c->source),"Local device timer");
    if (s_timer_end_ms || seconds || s_timer_due) {
        snprintf(c->buttons[0].label,25,"%s",s_timer_end_ms?"Pause":s_timer_due?"Dismiss":"Resume");
        snprintf(c->buttons[0].action,16,"%s",s_timer_end_ms?"pause":s_timer_due?"cancel":"resume");
        snprintf(c->buttons[1].label,25,"Cancel"); snprintf(c->buttons[1].action,16,"cancel");
    } else {
        snprintf(c->buttons[0].label,25,"5 min"); snprintf(c->buttons[0].action,16,"timer5");
        snprintf(c->buttons[1].label,25,"15 min"); snprintf(c->buttons[1].action,16,"timer15");
        snprintf(c->buttons[2].label,25,"25 min"); snprintf(c->buttons[2].action,16,"timer25");
    }
    muse_cards_submit(c); free(c);
    notebook_card();
}
static void local_action(const char *id, const char *action)
{
    if (!strcmp(action,"retry_saved") || !strcmp(action,"forget_saved")) {
        int index=0; cJSON *entry;
        if (!s_outbox_valid) return;
        cJSON_ArrayForEach(entry,s_outbox) {
            if (!strcmp(str(entry,"id"),id) && cJSON_IsTrue(cJSON_GetObjectItem(entry,"review"))) {
                cJSON *copy=cJSON_Duplicate(s_outbox,true); if (!copy) return;
                if (!strcmp(action,"retry_saved")) cJSON_DeleteItemFromObject(cJSON_GetArrayItem(copy,index),"review");
                else cJSON_DeleteItemFromArray(copy,index);
                if (persist_json(FS "/outbox.json",copy)) {
                    cJSON_Delete(s_outbox); s_outbox=copy; s_pending_actions=cJSON_GetArraySize(copy); s_action_retry=0;
                    muse_cards_remove(id); local_cards(); muse_state_set_caption("%s",!strcmp(action,"retry_saved")?"Saved action queued again":"Local change forgotten · check companion state");
                } else cJSON_Delete(copy);
                return;
            }
            index++;
        }
        return;
    }
    if (!strcmp(id,"local.notebook")) { notebook_action(action); return; }
    if (!strcmp(id,"local.power")) {
        pocket_profile_t profile;
        if (!strcmp(action,"responsive")) profile=POCKET_RESPONSIVE;
        else if (!strcmp(action,"balanced")) profile=POCKET_BALANCED;
        else if (!strcmp(action,"travel")) profile=POCKET_TRAVEL;
        else return;
        nvs_handle_t n; bool ok = nvs_open("muse_pocket",NVS_READWRITE,&n)==ESP_OK;
        if (ok) { ok=nvs_set_u8(n,"profile",profile)==ESP_OK && nvs_commit(n)==ESP_OK; nvs_close(n); }
        if (ok) s_profile=profile;
        muse_state_set_caption("%s",ok?"Power profile saved":"Profile was not saved"); local_cards(); return;
    }
    if (!strcmp(id,"local.timer")) {
        int64_t now=esp_timer_get_time()/1000;
        unsigned seconds=s_timer_remaining; bool running=false;
        if (!strcmp(action,"timer5")) { seconds=300; running=true; }
        else if (!strcmp(action,"timer15")) { seconds=900; running=true; }
        else if (!strcmp(action,"timer25")) { seconds=1500; running=true; }
        else if (!strcmp(action,"pause")) seconds=s_timer_end_ms>now?(unsigned)((s_timer_end_ms-now+999)/1000):0;
        else if (!strcmp(action,"resume")) running=seconds>0;
        else if (!strcmp(action,"cancel")) seconds=0;
        else return;
        bool ok=save_timer(seconds,running?now+seconds*1000LL:0,false);
        muse_state_set_caption("%s",ok?"Timer saved on device":"Timer was not saved"); local_cards(); return;
    }
    if (!s_fs || !s_outbox_valid || cJSON_GetArraySize(s_outbox)>=16) { muse_state_set_caption("Action NOT saved · storage unavailable or full"); return; }
    if (pending_action(id)) { muse_state_set_caption("Action already saved · awaiting confirmation"); return; }
    cJSON *item=NULL, *r;
    cJSON_ArrayForEach(r,s_reminders) if (!strcmp(str(r,"id"),id)) item=r;
    if (!item && !strcmp(action,"done")) cJSON_ArrayForEach(r,s_todos) if (!strcmp(str(r,"id"),id)) item=r;
    if (!item || (strcmp(action,"done") && strcmp(action,"snooze"))) { muse_state_set_caption("Item is not cached · reconnect before acting"); return; }
    if (!strcmp(action,"snooze") && !pocket_now()) { muse_state_set_caption("Snooze NOT saved · reconnect to set the clock, or use the local timer"); return; }
    cJSON *copy=cJSON_Duplicate(s_outbox,true), *entry=event("action"); char op[33]; random_id(op);
    cJSON_AddStringToObject(entry,"id",id); cJSON_AddStringToObject(entry,"action",action);
    cJSON_AddStringToObject(entry,"operation_id",op); cJSON_AddStringToObject(entry,"title",str(item,"title"));
    if (!strcmp(action,"snooze") && pocket_now()) cJSON_AddNumberToObject(entry,"snooze_until",pocket_now()+600);
    if (!copy || !entry || !cJSON_AddItemToArray(copy,entry)) { cJSON_Delete(copy); cJSON_Delete(entry); muse_state_set_caption("Action NOT saved · memory full"); return; }
    if (persist_json(FS "/outbox.json",copy)) {
        cJSON_Delete(s_outbox); s_outbox=copy; s_pending_actions=cJSON_GetArraySize(copy); s_action_retry=0;
        muse_state_set_caption("Saved locally · awaiting confirmation"); local_cards();
    } else { cJSON_Delete(copy); muse_state_set_caption("Action NOT saved · storage write failed"); }
}
static void ack_action(const char *operation, bool failed)
{
    if (!s_outbox_valid) return;
    int index=0; cJSON *a;
    cJSON_ArrayForEach(a,s_outbox) {
        if (!strcmp(str(a,"operation_id"),operation)) {
            cJSON *copy=cJSON_Duplicate(s_outbox,true); if (!copy) return;
            if (failed) { cJSON_DeleteItemFromObject(cJSON_GetArrayItem(copy,index),"review"); cJSON_AddBoolToObject(cJSON_GetArrayItem(copy,index),"review",true); }
            else cJSON_DeleteItemFromArray(copy,index);
            if (persist_json(FS "/outbox.json",copy)) {
                char id[65]; snprintf(id,sizeof(id),"%s",str(a,"id"));
                cJSON_Delete(s_outbox); s_outbox=copy; s_pending_actions=cJSON_GetArraySize(copy);
                if (!failed) muse_cards_remove(id);
                s_action_retry=0; local_cards();
            } else cJSON_Delete(copy);
            return;
        }
        index++;
    }
}
static void replay_outbox(void)
{
    static int64_t rendered;
    int64_t now=esp_timer_get_time()/1000;
    if (s_timer_end_ms && now >= s_timer_end_ms) {
        if (save_timer(0,0,true)) { muse_state_set_asleep(false); muse_state_set_caption("Focus timer finished"); local_cards(); }
    }
    if (now-rendered>=1000 && !muse_state_asleep()) { local_cards(); rendered=now; }
    if (!s_outbox_valid) return;
    cJSON *a;
    cJSON_ArrayForEach(a,s_outbox) {
        cJSON *until=cJSON_GetObjectItem(a,"snooze_until");
        if (!strcmp(str(a,"action"),"snooze") && !cJSON_IsTrue(cJSON_GetObjectItem(a,"review")) &&
            !cJSON_IsTrue(cJSON_GetObjectItem(a,"locally_alerted")) && cJSON_IsNumber(until) && pocket_now() && until->valuedouble<=pocket_now() && !pending_action(str(a,"id"))) {
            cJSON_AddBoolToObject(a,"locally_alerted",true);
            if (persist_json(FS "/outbox.json",s_outbox)) {
                muse_state_set_asleep(false); muse_state_set_caption("Snooze ended: %s",str(a,"title")); local_cards();
            } else cJSON_DeleteItemFromObject(a,"locally_alerted");
        }
    }
    if (!s_connected || !s_outbox_valid || now<s_action_retry) return;
    cJSON_ArrayForEach(a,s_outbox) {
        if (cJSON_IsTrue(cJSON_GetObjectItem(a,"review"))) continue;
        cJSON *copy=cJSON_Duplicate(a,true); if (!copy) return;
        cJSON_DeleteItemFromObject(copy,"title"); cJSON_DeleteItemFromObject(copy,"review");
        cJSON_DeleteItemFromObject(copy,"locally_alerted");
        send_json(copy); s_action_retry=now+30000; return;
    }
}
bool muse_pocket_wifi_nap(bool asleep_on_battery, bool force)
{
    bool nap=pocket_should_nap(&s_power_policy,s_profile,esp_timer_get_time()/1000,asleep_on_battery,muse_pocket_busy(),force);
    s_next_sync_seconds=nap?(uint32_t)(s_power_policy.wake_at/1000):0;
    return nap;
}
bool muse_pocket_ota_ready(void) { return s_connected && s_compatible && s_storage_checked && s_outbox_valid; }
static void send_health(void)
{
    cJSON *o=event("device.health"), *health=cJSON_AddObjectToObject(o,"health");
    muse_power_t power=muse_state_power(); size_t total=0,used=0;
    if (s_fs) esp_spiffs_info("pocket",&total,&used);
    cJSON_AddStringToObject(health,"firmware",esp_app_get_description()->version);
    cJSON_AddBoolToObject(health,"wifi_connected",muse_wifi_connected()); cJSON_AddBoolToObject(health,"muse_paired",muse_link_hatch_linked());
    if (power.battery_pct>=0 && power.battery_pct<=100) cJSON_AddNumberToObject(health,"battery_percent",power.battery_pct);
    cJSON_AddBoolToObject(health,"charging",power.charging);
    cJSON_AddNumberToObject(health,"free_internal_bytes",heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    cJSON_AddNumberToObject(health,"pocket_storage_free",total>=used?total-used:0);
    cJSON_AddStringToObject(health,"profile",pocket_profile_name(s_profile));
    if (s_next_sync_seconds && pocket_now()) cJSON_AddNumberToObject(health,"next_sync",pocket_now()+s_next_sync_seconds-esp_timer_get_time()/1000000);
    send_json(o);
}

bool muse_pocket_notebook_recording(void) { return s_notebook_recording; }
static bool save_notebook(void)
{
    cJSON *o=cJSON_CreateObject(), *markers=cJSON_AddArrayToObject(o,"markers");
    cJSON_AddStringToObject(o,"id",s_notebook_id); cJSON_AddNumberToObject(o,"segments",s_notebook_segments);
    cJSON_AddBoolToObject(o,"finishing",s_notebook_finish);
    for (unsigned i=0;i<s_notebook_segments;i++) if (s_notebook_markers[i]) cJSON_AddItemToArray(markers,cJSON_CreateNumber(i));
    bool ok=persist_json(FS "/notebook.json",o); cJSON_Delete(o); return ok;
}
static void load_notebook(void)
{
    cJSON *o=read_json_file(FS "/notebook.json",2048);
    if (!o) {
        if (!storage_file_missing(FS "/notebook.json")) s_notebook_error=true;
        return;
    }
    cJSON *count=cJSON_GetObjectItem(o,"segments");
    if (!safe_id(str(o,"id")) || !cJSON_IsNumber(count) || count->valueint<0 || count->valueint>MUSE_NOTEBOOK_MAX_SEGMENTS) { cJSON_Delete(o); s_notebook_error=true; return; }
    snprintf(s_notebook_id,sizeof(s_notebook_id),"%s",str(o,"id")); s_notebook_segments=count->valueint;
    s_notebook_finish=cJSON_IsTrue(cJSON_GetObjectItem(o,"finishing"));
    cJSON *marker;
    cJSON_ArrayForEach(marker,cJSON_GetObjectItem(o,"markers")) {
        if (cJSON_IsNumber(marker) && marker->valueint>=0 && marker->valueint<(int)s_notebook_segments) s_notebook_markers[marker->valueint]=true;
    }
    cJSON_Delete(o);
    /* A completed segment rename can precede its register write at power loss.
     * Recover its position from the immutable header before any upload/deletion. */
    DIR *dir=opendir(FS); struct dirent *entry;
    while (dir && (entry=readdir(dir))) {
        char id[33], path[100];
        if (!muse_pocket_note_id(entry->d_name,id) || !note_path(id,path)) continue;
        FILE *f=fopen(path,"rb"); if (!f) continue;
        uint8_t header[64]; fseek(f,0,SEEK_END); long size=ftell(f); rewind(f);
        size_t n=fread(header,1,sizeof(header),f); fclose(f); muse_notebook_segment_t segment;
        if (muse_notebook_decode(header,n,size>0?(size_t)size:0,&segment)>0 && !strcmp(segment.id,s_notebook_id)) {
            if (segment.position>=s_notebook_segments) s_notebook_segments=segment.position+1;
            if (segment.marked) s_notebook_markers[segment.position]=true;
        }
    }
    if (dir) closedir(dir);
    if (!save_notebook()) s_notebook_error=true;
    notebook_card();
}
static void notebook_card(void)
{
    muse_card_t *c=heap_caps_calloc(1,sizeof(*c),MUSE_BIG_CAPS); if (!c) return;
    snprintf(c->id,sizeof(c->id),"local.notebook"); snprintf(c->title,sizeof(c->title),"Walking notes & meetings");
    snprintf(c->body,sizeof(c->body),"%s\n%u segments saved\nTell participants before recording.\n\nUp to one hour online, or about four minutes offline when empty. Pauses when storage fills. Each segment is up to 15 seconds.",
             s_notebook_error?"Paused: saved notebook needs review":s_notebook_finish?"Waiting to finish syncing":s_notebook_recording?"Microphone ON - recording":s_notebook_id[0]?"Paused - microphone off":"Microphone off",s_notebook_segments);
    snprintf(c->source,sizeof(c->source),"%s",s_notebook_finish?"Saved locally; awaiting confirmation":"Recording creates an editable summary");
    if (!s_notebook_error && !s_notebook_finish) {
        snprintf(c->buttons[0].label,25,"%s",s_notebook_recording?"Pause":s_notebook_id[0]?"Resume":"Record");
        snprintf(c->buttons[0].action,16,"%s",s_notebook_recording?"pause":"record");
        if (s_notebook_id[0]) {
            snprintf(c->buttons[1].label,25,"Important"); snprintf(c->buttons[1].action,16,"mark");
            snprintf(c->buttons[2].label,25,"Finish"); snprintf(c->buttons[2].action,16,"finish");
        }
    } else if (s_notebook_error) {
        snprintf(c->buttons[0].label,25,"Retry"); snprintf(c->buttons[0].action,16,"retry");
    }
    muse_cards_submit(c); free(c);
}
static void notebook_action(const char *action)
{
    if (!strcmp(action,"retry") && s_notebook_error && !s_recording) {
        /* Reload the durable register and recover renamed segments. A malformed
         * or missing register stays blocked; never overwrite it with guesses. */
        cJSON *saved=read_json_file(FS "/notebook.json",2048);
        bool valid=saved && safe_id(str(saved,"id")); cJSON_Delete(saved);
        if (!valid) { muse_state_set_caption("Notebook register needs recovery · recordings retained"); return; }
        s_notebook_id[0]=0; s_notebook_segments=0; s_notebook_finish=false;
        memset(s_notebook_markers,0,sizeof(s_notebook_markers)); s_notebook_error=false;
        load_notebook(); s_notebook_retry=0; s_retry_at=0;
        muse_state_set_caption("%s",s_notebook_error?"Storage still unavailable · recordings retained":"Saved notebook ready to sync");
        notebook_card(); return;
    }
    if (s_notebook_error || s_notebook_finish) { muse_state_set_caption("Notebook saved · review or wait for sync"); return; }
    if (!strcmp(action,"record")) {
        if (s_recording || s_capture_language[0]) { muse_state_set_caption("Finish the current voice or interpreter session first"); return; }
        if (!s_notebook_id[0]) {
            random_id(s_notebook_id); s_notebook_segments=0; memset(s_notebook_markers,0,sizeof(s_notebook_markers));
            if (!save_notebook()) { s_notebook_id[0]=0; muse_state_set_caption("Notebook NOT started · storage unavailable"); return; }
        }
        if (!muse_input_request_talk()) muse_state_set_caption("Recording did not start · try again");
    } else if (!strcmp(action,"pause")) s_notebook_recording=false;
    else if (!strcmp(action,"mark")) {
        if (s_notebook_recording) s_notebook_mark=true;
        else if (s_notebook_segments) {
            bool prior=s_notebook_markers[s_notebook_segments-1];
            s_notebook_markers[s_notebook_segments-1]=true;
            if (!save_notebook()) { s_notebook_markers[s_notebook_segments-1]=prior; muse_state_set_caption("Important marker NOT saved"); return; }
        }
        muse_state_set_caption("Important moment marked");
    } else if (!strcmp(action,"finish") && s_notebook_id[0]) {
        s_notebook_recording=false; s_notebook_finish=true;
        if (!save_notebook()) { s_notebook_finish=false; muse_state_set_caption("Finish NOT saved · retry"); }
    }
    notebook_card();
}
static void notebook_tick(void)
{
    if (!s_notebook_finish || s_recording || s_queued>0 || !s_connected || s_notebook_error || uxQueueMessagesWaiting(s_work)>0) return;
    if (!s_notebook_segments) {
        if (remove_storage_file(FS "/notebook.json")) { s_notebook_id[0]=0; s_notebook_finish=false; notebook_card(); }
        return;
    }
    int64_t now=esp_timer_get_time()/1000; if (now<s_notebook_retry) return;
    cJSON *o=event("notebook.finish"), *markers=cJSON_AddArrayToObject(o,"markers");
    cJSON_AddStringToObject(o,"id",s_notebook_id); cJSON_AddNumberToObject(o,"segments",s_notebook_segments);
    for (unsigned i=0;i<s_notebook_segments;i++) if (s_notebook_markers[i]) cJSON_AddItemToArray(markers,cJSON_CreateNumber(i));
    send_json(o); s_notebook_retry=now+30000;
}
