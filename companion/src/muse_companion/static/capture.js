class MuseCapture extends AudioWorkletProcessor {
  constructor() { super(); this.samples = []; this.position = 0; this.pending = []; }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const x of channel) this.samples.push(x);
    const step = sampleRate / 16000;
    while (this.position + 1 < this.samples.length) {
      const i = Math.floor(this.position), f = this.position - i;
      const x = this.samples[i] * (1 - f) + this.samples[i + 1] * f;
      this.pending.push(Math.round(Math.max(-1, Math.min(1, x)) * 32767));
      this.position += step;
      if (this.pending.length === 320) {
        const output = new Int16Array(this.pending); this.pending = [];
        this.port.postMessage(output.buffer, [output.buffer]);
      }
    }
    const drop = Math.floor(this.position);
    this.samples.splice(0, drop); this.position -= drop;
    return true;
  }
}
registerProcessor('muse-capture', MuseCapture);

