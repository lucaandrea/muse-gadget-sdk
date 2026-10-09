"""Exercise the firmware's clock policy, connection windows and OTA health gate."""
import ctypes
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PocketPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        base = Path(cls.temp.name)
        (base / 'policy.c').write_text('''
#include "muse_pocket_policy.h"
static pocket_power_policy_t policy;
void reset(void) { policy=(pocket_power_policy_t){0}; }
bool step(int p, long long t, bool asleep, bool busy, bool force) {
 return pocket_should_nap(&policy,p,t,asleep,busy,force);
}
long long next(void) { return policy.wake_at; }
bool valid(int p,int s,bool storage,bool receipts,unsigned free,unsigned largest,bool local) {
 return pocket_compatible(p,s,storage,receipts,free,largest,local);
}
''')
        subprocess.run([os.environ.get('CC','cc'), '-std=c11','-Wall','-Wextra','-Werror','-shared','-fPIC',
                        '-I',str(ROOT/'components/muse'),str(base/'policy.c'),'-o',str(base/'policy.so')],check=True)
        cls.lib=ctypes.CDLL(str(base/'policy.so'))
        cls.lib.step.argtypes=[ctypes.c_int,ctypes.c_longlong,ctypes.c_bool,ctypes.c_bool,ctypes.c_bool]
        cls.lib.step.restype=ctypes.c_bool
        cls.lib.next.restype=ctypes.c_longlong
        cls.lib.valid.restype=ctypes.c_bool

    def setUp(self):
        self.lib.reset()

    def test_balanced_checks_in_after_five_minutes_without_waking_display(self):
        self.assertFalse(self.lib.step(1,1000,True,False,False))
        self.assertFalse(self.lib.step(1,120999,True,False,False))
        self.assertTrue(self.lib.step(1,121000,True,False,False))
        self.assertEqual(self.lib.next(),421000)
        self.assertFalse(self.lib.step(1,421000,True,False,False))
        self.assertFalse(self.lib.step(1,465999,True,False,False))
        self.assertTrue(self.lib.step(1,466000,True,False,False))

    def test_travel_wakes_on_user_or_usb_and_busy_audio_extends_window(self):
        self.lib.step(2,1000,True,False,False)
        self.assertTrue(self.lib.step(2,31000,True,False,False))
        self.assertEqual(self.lib.next(),1831000)
        self.assertFalse(self.lib.step(2,32000,False,False,False))
        self.assertFalse(self.lib.step(2,33000,True,True,False))
        self.assertFalse(self.lib.step(2,77999,True,False,False))
        self.assertTrue(self.lib.step(2,78000,True,False,False))

    def test_responsive_stays_connected_and_profile_change_exits_nap(self):
        self.lib.step(2,1000,True,False,True)
        self.assertFalse(self.lib.step(0,100000,True,False,True))
        self.assertEqual(self.lib.next(),0)

    def test_ota_requires_compatible_receipts_storage_and_memory(self):
        good=[1,5,True,True,24576,8192,True]
        self.assertTrue(self.lib.valid(*good))
        for index,value in [(0,2),(1,4),(2,False),(3,False),(4,24575),(5,8191),(6,False)]:
            broken=good.copy();broken[index]=value
            self.assertFalse(self.lib.valid(*broken))
