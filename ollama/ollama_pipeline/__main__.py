"""`python -m ollama_pipeline` - checks every host and configured model."""

import asyncio

from . import Registry, load_config
from .config import config_dir


async def main() -> int:
    registry = Registry(load_config())
    try:
        print(f"config     {config_dir()}")
        health = await registry.health()
        for name, info in health.items():
            state = f"ok (Ollama {info['version']})" if info["ok"] else "UNREACHABLE"
            print(f"host {name:<10} {info['url']:<32} {state}")

        missing = []
        print()
        for entry in await registry.list_models():
            cloud = entry.host not in health  # an OpenCode model: nothing to pull, no Ollama host
            state = "ready" if entry.available else "not available" if cloud else "not pulled"
            print(f"  {entry.id:<28} {entry.model:<24} {entry.host:<8} {state}")
            if not entry.available and not cloud and health[entry.host]["ok"]:
                missing.append(entry.model)

        if missing:
            print("\nPull the missing models with:")
            for model in dict.fromkeys(missing):
                print(f"  ollama pull {model}")
        return 0 if all(info["ok"] for info in health.values()) else 1
    finally:
        await registry.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
