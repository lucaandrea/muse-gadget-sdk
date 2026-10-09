"""Native notebook metadata and compressed PCM round-trip at segment boundaries."""
import ctypes
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class NotebookRecording(unittest.TestCase):
    def test_format_validation_and_compression(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            (folder/'notebook.c').write_text(r'''
#include <assert.h>
#include <stdlib.h>
#include <math.h>
#include "muse_notebook_recording.h"
#include "muse_adpcm.h"
int main(void) {
 uint8_t header[64];muse_notebook_segment_t original={.id="0123456789abcdef0123456789abcdef",.position=239,.marked=true,.pcm_bytes=480000},decoded;
 assert(muse_notebook_encode(header,&original));
 assert(muse_notebook_decode(header,64,120064,&decoded)==1);
 assert(decoded.position==239 && decoded.marked && !strcmp(decoded.id,original.id));
 assert(muse_notebook_decode(header,63,120064,&decoded)==-1);
 assert(muse_notebook_decode(header,64,120065,&decoded)==-1);
 for(int i=7;i<64;i++){header[i]^=1;assert(muse_notebook_decode(header,64,120064,&decoded)==-1);header[i]^=1;}
 original.position=240;assert(!muse_notebook_encode(header,&original));
 int16_t *pcm=malloc(480000), *out=malloc(480000);uint8_t *reference=malloc(120000);
 for(int i=0;i<240000;i++)pcm[i]=(int16_t)(10000*sin(i*.08));
 muse_adpcm_t encoder={0};muse_adpcm_encode_block(&encoder,pcm,240000,reference);
 encoder=(muse_adpcm_t){0};muse_adpcm_encode_block(&encoder,pcm,240000,(uint8_t *)pcm);
 assert(!memcmp(reference,pcm,120000)); /* In-place compression preserves unread samples. */
 muse_adpcm_t decoder={0};
 for(int i=0;i<120000;i+=320)muse_adpcm_decode_block(&decoder,reference+i,640,out+i*2);
 double squared=0;for(int i=100;i<240000;i++){double delta=out[i]-10000*sin(i*.08);squared+=delta*delta;}
 assert(sqrt(squared/239900)<500); /* Bounded error for a speech-band tone. */
 free(pcm);free(out);free(reference);return 0;
}
''')
            subprocess.run([os.environ.get('CC','cc'),'-std=c11','-Wall','-Wextra','-Werror','-I',str(ROOT/'components/muse'),str(folder/'notebook.c'),str(ROOT/'components/muse/muse_adpcm.c'),'-o',str(folder/'notebook')],check=True)
            subprocess.run([str(folder/'notebook')],check=True)
