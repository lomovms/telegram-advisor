from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .openclaw_client import OpenClawClient, OpenClawClientError


class CodexCliClient(OpenClawClient):
    def __init__(self, codex_bin: Path | None, model: str, timeout_seconds: float = 120.0) -> None:
        super().__init__(
            base_url="",
            token=None,
            model=model,
            backend="codex",
            timeout_seconds=timeout_seconds,
        )
        self.codex_bin = _resolve_codex_bin(codex_bin)

    def available(self) -> bool:
        return self.codex_bin is not None and self.codex_bin.exists()

    def login_status(self) -> tuple[bool, str]:
        if not self.available():
            return False, "Codex CLI не найден."
        try:
            completed = subprocess.run(
                [str(self.codex_bin), "login", "status"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                creationflags=_no_window_flags(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        message = (completed.stdout or completed.stderr).strip()
        return completed.returncode == 0, message

    def start_login(self) -> None:
        if not self.available():
            raise OpenClawClientError("Codex CLI не найден в установленном приложении.")
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        try:
            subprocess.Popen(
                [str(self.codex_bin), "login"],
                cwd=str(Path.home()),
                creationflags=flags,
            )
        except OSError as exc:
            raise OpenClawClientError(f"Не удалось открыть вход в Codex: {exc}") from exc

    def _create_response(self, instructions: str, text: str) -> str:
        if not self.available():
            raise OpenClawClientError("Codex CLI не найден.")

        prompt = "\n".join(
            [
                "You are running as a local engine for a desktop Telegram assistant.",
                "Return only the final answer requested by the instructions.",
                "If the instructions ask for JSON, return valid JSON only, without Markdown fences.",
                "",
                "<instructions>",
                instructions or "Return a concise answer.",
                "</instructions>",
                "",
                "<input>",
                text or "",
                "</input>",
            ]
        )

        with tempfile.TemporaryDirectory(prefix="telegram-advisor-codex-") as run_dir:
            output_path = Path(run_dir) / "last-message.txt"
            args = [
                str(self.codex_bin),
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--output-last-message",
                str(output_path),
            ]
            if self.model:
                args.extend(["--model", self.model])
            args.append("-")
            try:
                completed = subprocess.run(
                    args,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                    cwd=run_dir,
                    creationflags=_no_window_flags(),
                )
            except subprocess.TimeoutExpired as exc:
                raise OpenClawClientError("Codex не ответил вовремя.") from exc
            except OSError as exc:
                raise OpenClawClientError(f"Не удалось запустить Codex: {exc}") from exc

            if completed.returncode != 0:
                details = (completed.stderr or completed.stdout).strip()[-1200:]
                raise OpenClawClientError(f"Codex завершился с ошибкой: {details}")
            if not output_path.exists():
                raise OpenClawClientError("Codex не создал файл ответа.")
            answer = output_path.read_text(encoding="utf-8", errors="replace").strip()
            if not answer:
                raise OpenClawClientError("Codex вернул пустой ответ.")
            return answer


def _resolve_codex_bin(configured: Path | None) -> Path | None:
    if configured and configured.exists():
        return configured.resolve()
    env_value = os.getenv("CODEX_BIN", "").strip()
    if env_value and Path(env_value).exists():
        return Path(env_value).resolve()
    found = shutil.which("codex")
    return Path(found).resolve() if found else None


def _no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)
