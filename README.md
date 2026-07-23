# QQ Official Hub

A security-first AstrBot plugin for visual QQ Official Markdown + Keyboard
panels. The default in-group command will be `/头条卡片`.

## Current stage

The repository contains only adapter-independent callback-scope validation and
tests. The required QQ Official `INTERACTION_CREATE` bridge is absent from the
reviewed AstrBot 4.26.7 adapter, so no fake or monkey-patched button runtime is
included.

See [`docs/ADAPTER_GAP.md`](docs/ADAPTER_GAP.md).
