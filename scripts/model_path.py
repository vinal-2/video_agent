from pathlib import Path
from llama_cpp import Llama
from llama_cpp.llama_chat_format import Llava15ChatHandler

MODEL_ROOT = Path.home() / ".cache" / "lm-studio" / "models" / "moondream" / "moondream-2b-2025-04-14-4bit"
TEXT_MODEL = MODEL_ROOT / "moondream2-text-model-f16.gguf"
MM_PROJ = MODEL_ROOT / "moondream2-mmproj-f16.gguf"

handler = Llava15ChatHandler(
    clip_model_path=str(MM_PROJ),
)

llm = Llama(
    model_path=str(TEXT_MODEL),
    chat_handler=handler,
    n_ctx=2048,
    n_gpu_layers=0,
)

response = llm.create_chat_completion(
    messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe this image."},
            {"type": "image_url", "image_url": {"url": "file:///C:/Users/vinal/Downloads/Phone Link/New folder/Screenshot_20260218_004550_Instagram.jpg"}},
        ],
    }]
)

print(response["choices"][0]["message"]["content"])
