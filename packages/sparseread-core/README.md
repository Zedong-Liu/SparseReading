# sparseread

Framework-neutral SparseRead protocol implementation. This distribution owns
the readers, FileCard/EvidencePack models, benefit gate, runtime, tool schemas,
and JSONL bridge server. It does not import NanoBot, OpenCode, or OpenClaw.

Framework packages depend on this distribution and provide their own bridge
classifier, lifecycle, installation, and prompt integration.

```bash
pip install "sparseread[all]"
```

Project documentation and source are available at
<https://github.com/Zedong-Liu/SparseReading>.
