"""Run real local storage/action functions against a temporary filesystem."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    import re
    match = re.search(r'^static [^\n;]+\b'+name+r'\([^;]*?\)\n\{', source, re.M)
    if not match:
        raise AssertionError(name)
    opening = source.index('{', match.start())
    depth, end = 1, opening+1
    while depth:
        depth += (source[end]=='{')-(source[end]=='}')
        end += 1
    return source[match.start():end]


class PocketOutbox(unittest.TestCase):
    def test_power_loss_replay_ack_failures_and_local_timers(self):
        source = (ROOT/'components/muse/muse_pocket.c').read_text()
        cjson = ROOT/'managed_components/espressif__cjson/cJSON'
        self.assertTrue((cjson/'cJSON.c').exists(), 'Run an IDF configure/build first')
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            (base/'lvgl.h').write_text('typedef struct lv_obj_t lv_obj_t;\n')
            prelude=r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <time.h>
#include <sys/stat.h>
#include "cJSON.h"
#include "muse_cards.h"
#include "muse_pocket_policy.h"
#define FS "."
#define MUSE_BIG_CAPS 0
#define NVS_READWRITE 0
#define ESP_OK 0
#define ESP_LOGE(...) ((void)0)
static bool s_fs=true,s_outbox_valid=true,s_connected=false,s_storage_checked;
static int s_pending_actions;
static pocket_profile_t s_profile=POCKET_BALANCED;
static cJSON *s_outbox,*s_reminders,*s_todos;
static int64_t s_action_retry,s_next_sync_seconds,s_synced_at;
static int64_t s_accounts_at;
static char s_account_summary[257],s_tools_url[321];
static time_t s_server_time=2000000000;
static int64_t clock_ms=1000;
static time_t unset_time(time_t *out){if(out)*out=0;return 0;}
#define time unset_time
static char caption[400],sent[4096];
static muse_card_t connection_card;
static unsigned sends,identities;
static void *heap_caps_calloc(size_t n,size_t z,int caps){(void)caps;return calloc(n,z);}
static int64_t esp_timer_get_time(void){return clock_ms*1000;}
static bool muse_wifi_connected(void){return s_connected;}
static bool muse_link_hatch_linked(void){return false;}
static bool muse_state_asleep(void){return true;}
static void muse_state_set_asleep(bool value){(void)value;}
#include <stdarg.h>
static void muse_state_set_caption(const char *fmt,...){va_list v;va_start(v,fmt);vsnprintf(caption,sizeof(caption),fmt,v);va_end(v);}
void muse_cards_submit(const muse_card_t *card){assert(strlen(card->id)<65);if(!strcmp(card->id,"local.power"))connection_card=*card;}
void muse_cards_remove(const char *id){(void)id;}
typedef int nvs_handle_t;
static int nvs_open(const char *s,int mode,int *n){(void)s;(void)mode;*n=1;return 0;}
static int nvs_set_u8(int n,const char *key,unsigned value){(void)n;(void)key;(void)value;return 0;}
static int nvs_commit(int n){(void)n;return 0;}
static void nvs_close(int n){(void)n;}
static void random_id(char out[33]){snprintf(out,33,"%032x",++identities);}
static bool send_json(cJSON *o){char *text=cJSON_PrintUnformatted(o);snprintf(sent,sizeof(sent),"%s",text);free(text);cJSON_Delete(o);sends++;return true;}
static void notebook_card(void) {}
static void notebook_action(const char *action) { (void)action; }
static void local_cards(void);
static time_t pocket_now(void);
static bool s_write_fail;
static bool s_rename_fail;
static int spiffs_rename(const char *from,const char *to){
 if(!access(to,F_OK) || (s_rename_fail && strstr(from,".tmp"))){errno=EIO;return -1;}
 return rename(from,to);
}
#define rename spiffs_rename
'''
            names=['str','safe_id','event','recover_storage_file','storage_file_missing','remove_storage_file','write_atomic','pocket_now','read_json_file','persist_json','pending_action','save_timer','load_local','local_cards','update_cache','local_action','ack_action','replay_outbox']
            bodies=[]
            for name in names:
                body=function(source,name)
                if name=='write_atomic':
                    body=body.replace('{','{\n    if (s_write_fail) return false;',1)
                bodies.append(body)
            main=r'''
int main(void){
 /* Reproduce SPIFFS rename conflicts, then interrupted replacement windows. */
 assert(write_atomic("./replace.json","{\"version\":1}",13));
 assert(write_atomic("./replace.json","{\"version\":2}",13));
 s_rename_fail=true;assert(!write_atomic("./replace.json","{\"version\":3}",13));s_rename_fail=false;
 cJSON *saved=read_json_file("./replace.json",100);assert(cJSON_GetObjectItem(saved,"version")->valueint==2);cJSON_Delete(saved);
 assert(!rename("./replace.json","./replace.json.bak"));
 assert(!storage_file_missing("./replace.json"));
 saved=read_json_file("./replace.json",100);assert(cJSON_GetObjectItem(saved,"version")->valueint==2);cJSON_Delete(saved);
 /* A new committed destination wins over its stale backup. Deletion removes both. */
 assert(write_atomic("./replace.json.bak","{\"version\":1}",13));
 saved=read_json_file("./replace.json",100);assert(cJSON_GetObjectItem(saved,"version")->valueint==2);cJSON_Delete(saved);
 assert(remove_storage_file("./replace.json") && storage_file_missing("./replace.json"));
 load_local();assert(s_outbox_valid && s_pending_actions==0);
 update_cache("{\"accounts\":{\"summary\":\"Slack: check access in Tools\",\"tools_url\":\"https://companion.example/#view=tools\"},\"reminders\":[],\"todos\":[]}",true);
 assert(strstr(connection_card.body,"not live") && strstr(connection_card.body,"check access"));
 s_connected=true;local_cards();assert(!strstr(connection_card.body,"not live"));
 assert(!strcmp(connection_card.handoff,"https://companion.example/#view=tools"));
 clock_ms+=46000;local_cards();assert(strstr(connection_card.body,"not live"));
 update_cache("{\"accounts\":{\"summary\":\"Saved status\",\"tools_url\":\"javascript:alert(1)\"},\"reminders\":[],\"todos\":[]}",false);
 assert(!connection_card.handoff[0]);s_connected=false;
 cJSON_Delete(s_reminders);
 s_reminders=cJSON_Parse("[{\"id\":\"reminder-one\",\"title\":\"Review brief\"}]");
 s_server_time=0;local_action("reminder-one","snooze");assert(s_pending_actions==0 && strstr(caption,"Snooze NOT saved"));
 s_server_time=2000000000;
 local_action("reminder-one","done");assert(s_pending_actions==1 && strstr(caption,"Saved locally"));
 replay_outbox();assert(sends==0);
 char operation[33];snprintf(operation,sizeof(operation),"%s",str(cJSON_GetArrayItem(s_outbox,0),"operation_id"));
 cJSON_Delete(s_outbox);s_outbox=NULL;load_local();assert(s_pending_actions==1);
 s_connected=true;replay_outbox();assert(sends==1 && strstr(sent,operation));
 clock_ms+=31000;replay_outbox();assert(sends==2 && strstr(sent,operation));
 s_write_fail=true;ack_action(operation,false);assert(s_pending_actions==1);
 s_write_fail=false;ack_action(operation,false);assert(s_pending_actions==0);
 ack_action(operation,false);assert(s_pending_actions==0);
 local_action("reminder-one","snooze");assert(s_pending_actions==1);
 cJSON *a=cJSON_GetArrayItem(s_outbox,0);
 double deadline=cJSON_GetObjectItem(a,"snooze_until")->valuedouble;
 snprintf(operation,sizeof(operation),"%s",str(a,"operation_id"));
 clock_ms+=120000;replay_outbox();cJSON *packet=cJSON_Parse(sent);
 assert(cJSON_GetObjectItem(packet,"snooze_until")->valuedouble==deadline);cJSON_Delete(packet);
 ack_action(operation,true);unsigned before=sends;clock_ms+=31000;replay_outbox();assert(sends==before);
 local_action("unknown","approve");assert(s_pending_actions==1);
 local_action("reminder-one","retry_saved");assert(s_pending_actions==1);
 replay_outbox();assert(sends==before+1 && strstr(sent,operation));
 ack_action(operation,true);ack_action(operation,true);
 s_write_fail=true;local_action("reminder-one","forget_saved");assert(s_pending_actions==1);
 s_write_fail=false;local_action("reminder-one","forget_saved");assert(s_pending_actions==0);
 local_action("reminder-one","snooze");assert(s_pending_actions==1);
 s_connected=false;clock_ms+=601000;replay_outbox();assert(strstr(caption,"Snooze ended"));
 assert(!pending_action("reminder-one"));
 local_action("reminder-one","done");assert(s_pending_actions==2 && pending_action("reminder-one"));
 local_action("local.timer","timer5");assert(s_timer_end_ms==clock_ms+300000);
 clock_ms+=60000;local_action("local.timer","pause");assert(s_timer_remaining==240 && !s_timer_end_ms);
 local_action("local.timer","resume");clock_ms+=240000;replay_outbox();assert(s_timer_due && !s_timer_end_ms);
 local_action("local.timer","cancel");assert(!s_timer_due && !s_timer_remaining);
 s_write_fail=true;local_action("local.timer","timer15");assert(!s_timer_end_ms);
 s_write_fail=false;
 FILE *f=fopen("./outbox.json","wb");fputs("damaged",f);fclose(f);
 cJSON_Delete(s_outbox);s_outbox=NULL;load_local();assert(!s_outbox_valid);
 before=sends;clock_ms+=31000;replay_outbox();assert(sends==before);
 local_action("reminder-one","done");assert(strstr(caption,"NOT saved"));
 cJSON_Delete(s_outbox);cJSON_Delete(s_reminders);cJSON_Delete(s_todos);
 (void)s_storage_checked;
 return 0;
}
'''
            (base/'outbox.c').write_text(prelude+'\nstatic int64_t s_timer_end_ms;\nstatic unsigned s_timer_remaining;\nstatic bool s_timer_due;\n'+'\n'.join(bodies)+main)
            subprocess.run([os.environ.get('CC','cc'),'-std=c11','-Wall','-Wextra','-Werror','-I',str(base),'-I',str(ROOT/'components/muse'),'-I',str(cjson),str(base/'outbox.c'),str(cjson/'cJSON.c'),'-o',str(base/'outbox')],check=True)
            subprocess.run([str(base/'outbox')],cwd=base,check=True)
