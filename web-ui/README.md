# Telegram Advisor Web UI

React + Tailwind frontend for the Telegram advisor. The desktop shell is Tauri;
the local Python API continues to own Telegram MTProto, SQLite and AI calls.

Desktop launch from the project root:

```powershell
.\start_web_advisor.cmd
```

The release executable is `src-tauri/target/release/telegram-advisor.exe`.
For development use `pnpm --dir web-ui exec tauri dev`. The first Tauri build
requires Rust toolchain and Windows C++ build tools. The web UI can be checked
independently with `pnpm --dir web-ui run build`.
