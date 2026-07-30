# Offline Disaster Triage Assistant — "AI off the Grid"

100% local pipeline: **Ollama + Gemma 3 4B (4-bit quantized)** → structured JSON triage
data → Streamlit dashboard, with an optional FastAPI webhook so a phone on the same
Wi-Fi hotspot can forward real SMS/WhatsApp text with no internet and no cloud APIs.

**Target hardware: AMD Ryzen 7 AI 350, 16GB DDR5, integrated Radeon GPU (RDNA 3.5),
Windows 11.** This is a meaningfully faster machine than the original i3-1215U —
you have a real iGPU Ollama can offload layers to, not just more CPU cores — so
several of the original "keep it minimal" tradeoffs can be relaxed. See the GPU
section below.

## Project layout

```
disaster-triage/
├── llm.py              # shared model wrapper, robust JSON parsing (no crashes on bad output)
├── db.py                # SQLite queue shared by the dashboard and the webhook
├── app.py                # original CLI prototype, now crash-proof (demo fallback)
├── streamlit_app.py     # main judge-facing UI: manual tester, batch CSV, live queue
├── webhook.py             # FastAPI endpoint that receives forwarded phone SMS
├── fewshot.py            # curated + operator-corrected few-shot examples
├── sample_messages.csv  # 10 mock messages to demo batch import
├── Modelfile             # system prompt + generation params
└── requirements.txt
```

## 1. One-time setup

```bash
# Pull the base model and build the custom Modelfile (you likely already did this)
ollama pull gemma3:4b
ollama create disaster-gemma -f Modelfile

# Python deps
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### GPU acceleration (new — worth enabling on this machine)

The Ryzen AI 350's integrated Radeon iGPU (RDNA 3.5) is usable by Ollama via its
**Vulkan** backend, which is enabled by default in current Ollama builds on
Windows — no ROCm install needed (ROCm on Windows doesn't support Ryzen AI APUs
at all; Vulkan is the actual working path for this chip). To confirm it's active:

```bash
ollama run disaster-gemma "test"
```
then check `ollama ps` or the server log for a Vulkan device line rather than
"100% CPU". If you see both a Vulkan *and* a ROCm entry for the same GPU with
suspiciously large combined VRAM, that's a known double-counting quirk on
Ryzen AI iGPUs — force Vulkan-only by setting `CUDA_VISIBLE_DEVICES=-1` as a
user environment variable before launching Ollama.

Net effect: noticeably faster generation than the original 7-10 tok/s estimate,
which is why the batch-size and token limits below are relaxed compared to the
i3 build. The onboard NPU (XDNA2) is **not** used by Ollama today — that's a
separate SDK (Ryzen AI Software / ONNX) and out of scope unless you want to
rebuild the inference path around it later.

Keep `temperature 0.1` in the Modelfile for consistent JSON. `num_predict` has
been raised from 256 → 384 (see Modelfile) since the extra headroom no longer
costs much latency on this hardware, giving the model more room for the
`action_required`/`recommended_resources` fields on complex, multi-person
messages without truncating.

## 2. Run everything for the demo

Open **two terminals** (both from inside `disaster-triage/`, venv activated):

**Terminal 1 — the webhook (only needed if you're forwarding real phone SMS):**
```bash
uvicorn webhook:app --host 0.0.0.0 --port 8000
```

**Terminal 2 — the dashboard:**
```bash
streamlit run streamlit_app.py --server.address 0.0.0.0
```

Streamlit will print a **Network URL** like `http://192.168.x.x:8501` — that's what
you open on a phone. If you don't see it, find your laptop's LAN IP with `ipconfig`
(look under the Wi-Fi adapter once your hotspot is on).

Both processes share `triage_queue.db` (SQLite, created automatically), so anything
posted to the webhook shows up in the dashboard's "Live Triage Queue" tab without any
extra wiring.

## 3. Connecting a phone (no internet, local hotspot only)

1. Turn on the laptop's mobile hotspot, or connect both devices to any local
   router/hotspot that has **no internet uplink** (proving the "off the grid" claim).
2. On the phone, browse to `http://<laptop-lan-ip>:8501` — this is the dashboard,
   fully usable from a phone browser (the UI has mobile-responsive styling built in).
3. Optional — real SMS forwarding: install a local "SMS to Webhook" forwarder app
   (several free ones on F-Droid/Play work purely over LAN, no cloud relay) and set
   its target URL to `http://<laptop-lan-ip>:8000/sms`, with a JSON body shaped like:
   ```json
   {"message": "the SMS text", "sender": "+1555..."}
   ```
   Every forwarded text is triaged the moment it lands and appears in the queue.

## 4. What to show the judges

- **Manual Tester tab** — type a message, get structured JSON in under a couple
  seconds, fully offline.
- **Batch CSV Import tab** — upload `sample_messages.csv` (or a slice of the Kaggle
  Multilingual Disaster Response set / TREC-IS), watch the live progress bar, get a
  table auto-sorted High → Low urgency.
- **Live Triage Queue tab** — the unifying view: shows manual entries, batch
  entries, and anything forwarded from a phone, all sorted by urgency.
- Turn the laptop's Wi-Fi off entirely beforehand and demo anyway — that's the
  strongest possible proof of "off the grid."

## 5. Fallback: on-device mobile inference (no laptop at all)

If judges want to see the model running natively on a phone with zero dependency on
the laptop, the standard offline path is **Gemma via MediaPipe LLM Inference API**
on Android:

1. Convert/obtain a MediaPipe-compatible `.task` bundle for a small Gemma model
   (Google publishes ready-made `.task` files for Gemma 2B/3 1B-2B sized for mobile;
   grab one from the MediaPipe Gemma model page or convert with the AI Edge
   Torch/MediaPipe conversion tools).
2. Add the MediaPipe Tasks dependency to an Android Studio project:
   ```gradle
   implementation 'com.google.mediapipe:tasks-genai:latest.release'
   ```
3. Bundle the `.task` file in the app's assets (or push it to device storage), then
   initialize inference:
   ```kotlin
   val options = LlmInference.LlmInferenceOptions.builder()
       .setModelPath("/data/local/tmp/gemma.task")
       .setMaxTokens(256)
       .setTemperature(0.1f)
       .build()
   val llmInference = LlmInference.createFromOptions(context, options)
   val result = llmInference.generateResponse(prompt)
   ```
4. Reuse the exact same system prompt from the Modelfile (the JSON schema) so the
   on-device output format matches the laptop dashboard's parser.

This is a good "future work / architecture" slide even if you don't have time to
fully build it — it shows the judges the same offline design travels to a phone
with no laptop at all, which directly matches the "mobile, or IoT" language in the
track description.

## 6. Improving accuracy without fine-tuning (hours, not days)

Real fine-tuning (LoRA + GGUF re-export into Ollama) needs a GPU session plus a
merge/convert/quantize cycle — not realistic in a few hours. Three things here get
real accuracy gains instead, no retraining required:

1. **Rebuild `disaster-gemma` from the updated `Modelfile`.** The original system
   prompt never defined what "High/Medium/Low" actually meant, so the model was
   guessing. The new one gives explicit criteria and an "if unsure, go higher"
   rule (under-triaging a real emergency is the worse failure mode):
   ```bash
   ollama create disaster-gemma -f Modelfile
   ```
2. **`fewshot.py` — curated examples on every request.** Six hand-written
   message→JSON pairs covering the edge cases zero-shot prompting usually gets
   wrong (ambiguous medical, non-emergency status updates, infrastructure hazards
   vs. direct danger) are prepended to every prompt automatically via
   `llm.build_prompt()`. This is in-context learning, not weight updates, but it
   measurably reduces schema drift and misjudged urgency.
3. **Live correction feedback loop.** In the Manual Tester tab, expand
   "🛠️ Wrong triage? Fix it" under any result to correct it. Corrections are saved
   to `triage_queue.db` and the next time a *similar* message comes in (matched by
   simple keyword overlap, no embeddings needed — stays fast on the i3), that
   correction is automatically added as an extra few-shot example. Correct a few
   messages before your demo and accuracy visibly improves as you go — and it's a
   genuinely good talking point: **"the system incorporates operator feedback in
   real time, fully offline, with no retraining step."**

If you get a longer runway later, the real next step is: export corrections +
curated examples as a JSONL dataset, LoRA fine-tune Gemma 3 4B on a free Colab T4
(e.g. with Unsloth, which is fast for Gemma), merge the adapter, convert to GGUF,
and `ollama create` a new model from that file. Happy to write that pipeline when
you have the time for it — it's a bigger job than fits in an hours-only window.

## 7. Notes on robustness during the live demo

- `llm.py` strips markdown fences and grabs the outermost `{...}` block before
  parsing, and falls back to a labeled "PARSE_ERROR" record instead of throwing —
  so a single malformed generation can't crash the dashboard mid-demo.
- Batch import has a row-limit slider so you don't accidentally kick off a
  200-message run on stage and stall the i3 CPU.
