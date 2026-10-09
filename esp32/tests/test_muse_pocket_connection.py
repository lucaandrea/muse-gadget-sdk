"""Run the production connection supervisor against a simulated IDF client."""
import ctypes
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class PocketConnectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        directory = Path(cls.temp.name)
        source = (Path(__file__).resolve().parents[1] / "components/muse/muse_pocket.c").read_text()
        start = source.index("static void service_connection(void)")
        opening = source.index("{", start)
        depth, end = 1, opening + 1
        while depth:
            depth += (source[end] == "{") - (source[end] == "}")
            end += 1
        supervisor = source[start:end]
        harness = r"""
#include <stdbool.h>
#include <stddef.h>
typedef struct { int state; } muse_ble_status_t;
enum { MUSE_BLE_CONNECTED=2, ESP_OK=0 };
static const char *TAG="test";
static void *s_ws=(void *)1;
static bool s_ws_started, s_setup_paused, s_restart_ws;
static bool s_connected, s_ready, s_inflight, s_done;
static size_t s_rx_len;
static bool wifi, phone, fail_start, running;
static int starts, stops, delays;
#define ESP_LOGI(tag, ...) ((void)(tag))
#define pdMS_TO_TICKS(n) (n)
static void muse_ble_status(muse_ble_status_t *out) { out->state=phone?2:0; }
static bool muse_wifi_connected(void) { return wifi; }
static int esp_websocket_client_start(void *p) {
    (void)p; starts++; if(fail_start) return -1; running=true; return ESP_OK;
}
static int esp_websocket_client_stop(void *p) {
    (void)p; stops++; if(!running) return -1; running=false; return ESP_OK;
}
static void vTaskDelay(int n) { delays+=n; }
""" + supervisor + r"""
void reset(void) {
    s_ws_started=s_setup_paused=s_restart_ws=false;
    s_connected=s_ready=s_inflight=s_done=false; s_rx_len=0;
    wifi=phone=fail_start=running=false; starts=stops=delays=0;
}
void step(int online, int pairing, int failure) {
    wifi=online; phone=pairing; fail_start=failure; service_connection();
}
void connected(void) { s_connected=s_ready=s_inflight=true; s_rx_len=42; }
void closed(void) { running=false; s_restart_ws=true; }
int stat(int field) {
    switch(field) {
    case 0: return starts; case 1: return stops; case 2: return running;
    case 3: return s_setup_paused; case 4: return s_connected||s_ready||s_inflight||s_rx_len;
    case 5: return delays; default: return -1;
    }
}
"""
        path = directory / "connection.c"
        path.write_text(harness)
        lib = directory / "connection.so"
        subprocess.run([os.environ.get("CC", "cc"), "-shared", "-fPIC", "-std=c11",
                        "-Wall", "-Wextra", "-Werror", str(path), "-o", str(lib)], check=True)
        cls.lib = ctypes.CDLL(str(lib))
        cls.lib.step.argtypes = [ctypes.c_int] * 3
        cls.lib.stat.argtypes = [ctypes.c_int]
        cls.lib.stat.restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.lib.reset()

    def test_phone_setup_releases_network_worker_once_and_resumes(self):
        self.lib.step(1, 0, 0)
        self.lib.connected()
        for _ in range(4):
            self.lib.step(1, 1, 0)
        self.assertEqual(self.lib.stat(0), 1)
        self.assertEqual(self.lib.stat(1), 1)
        self.assertEqual(self.lib.stat(2), 0)
        self.assertEqual(self.lib.stat(3), 1)
        self.assertEqual(self.lib.stat(4), 0)
        self.lib.step(1, 0, 0)
        self.assertEqual(self.lib.stat(0), 2)
        self.assertEqual(self.lib.stat(2), 1)

    def test_no_worker_without_wifi_and_reconnect_after_wifi_returns(self):
        self.lib.step(0, 0, 0)
        self.assertEqual(self.lib.stat(0), 0)
        self.lib.step(1, 0, 0)
        self.lib.step(0, 0, 0)
        self.assertEqual(self.lib.stat(2), 0)
        self.lib.step(1, 0, 0)
        self.assertEqual(self.lib.stat(0), 2)

    def test_graceful_close_does_not_bypass_pairing_pause(self):
        self.lib.step(1, 0, 0)
        self.lib.closed()
        self.lib.step(1, 1, 0)
        self.assertEqual(self.lib.stat(0), 1)
        self.assertEqual(self.lib.stat(2), 0)
        self.lib.step(1, 0, 0)
        self.assertEqual(self.lib.stat(0), 2)

    def test_graceful_close_waits_for_old_task_cleanup(self):
        self.lib.step(1, 0, 0)
        self.lib.closed()
        self.lib.step(1, 0, 0)
        self.assertEqual(self.lib.stat(0), 2)
        self.assertGreaterEqual(self.lib.stat(5), 1000)

    def test_failed_allocation_is_retried_after_setup(self):
        self.lib.step(1, 0, 1)
        self.assertEqual(self.lib.stat(2), 0)
        self.lib.step(1, 1, 0)
        self.assertEqual(self.lib.stat(0), 1)
        self.lib.step(1, 0, 0)
        self.assertEqual(self.lib.stat(0), 2)
        self.assertEqual(self.lib.stat(2), 1)
