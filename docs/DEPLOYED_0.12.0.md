# Deployed v0.12.0 snapshot

This repository baseline was resynchronized on **2026-10-17** from the source directory actually deployed on the Raspberry Pi:

```text
/opt/house-audio-server
```

The live server identifies itself as:

```python
SERVICE_NAME = "house-audio-server"
SERVICE_VERSION = "0.12.0"
```

This snapshot is the authority for the v0.12.0 source baseline. It was used instead of reconstructing missing revisions from chat history.

## Imported live files

| File | SHA-256 |
| --- | --- |
| `house_audio_server.py` | `92d872a591affbd00649f8f971c6f5ba502fcf4397b7a903ee05246083d6c987` |
| `radio_history.py` | `e68e85f5f50a85ecccb34863c20367f4206ee67e4a6d49071e9eff540265cbbf` |
| `radio_listening.py` | `f11e51e49c2aec26912b566d23af13772345f6476efa255c1e3e085306f06395` |
| `radio_metadata.py` | `715ce8ba5c61490cb367cc72fd8efe1b148f0cb09ce065934283ec3f120958ff` |
| `radio_stations.py` | `3885be51b2f1dd95e0562cf9accc3f13bea2064a91883172f501675b58452ab1` |
| `state_file.py` | `9e55694f75053d25158d92c805f844453b081a392ec0dc1041d91ef9de0b3e14` |
| `subwoofers.py` | `43ed1ec4b06af3eb4f2821590997f57277040ad05aa0702ebcfd7e53cc3eb2d0` |
| `web/subwoofers.css` | `af679993d3eadfd79a1c51565c0da22ed305110034207d15c6afb9594b47d5b1` |
| `web/subwoofers.html` | `99f617dcda890fbed3cb4fbe814c7a2e6f2289f4e36640b4808e2cadcd370dca` |
| `web/subwoofers.js` | `1ff94988e3da7e301e0112e852eddc3a7e91f67b0e8152eb98d8dded7be8d46a` |

All ten imported repository files were verified byte-for-byte against the live archive before merge.

The extracted Python files also passed `python3 -m py_compile`.

## Deployment structure

The live service continues to use:

```text
WorkingDirectory=/opt/house-audio-server
ExecStart=/usr/bin/python3 /opt/house-audio-server/house_audio_server.py
```

The existing systemd unit already matched the deployed unit, so it did not need a functional change.

The installer was updated with this baseline so a fresh deployment copies all required v0.12.0 Python modules and the `web/` assets, rather than only `house_audio_server.py`.

## What is present in v0.12.0

Based on the deployed source, this baseline includes:

- MPD queue/transport/library control
- Snapserver renderer observation and controller leases
- passive-session start/drain/pause policy
- runtime passive default-folder selection
- radio station bookmarks and stream probing
- radio metadata normalization
- persisted last-played radio history
- radio listening-time accounting/ranking
- subwoofer registration and dependent-renderer demand logic
- subwoofer status reporting
- built-in subwoofer settings page under `/subwoofers`

This file records observed source contents only; it does not invent missing intermediate release history between the old repository state and v0.12.0.
