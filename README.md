```
███████╗██████╗ ██╗██████╗  █████╗ ██╗   ██╗
██╔════╝██╔══██╗██║██╔══██╗██╔══██╗╚██╗ ██╔╝
█████╗  ██████╔╝██║██║  ██║███████║ ╚████╔╝ 
██╔══╝  ██╔══██╗██║██║  ██║██╔══██║  ╚██╔╝  
██║     ██║  ██║██║██████╔╝██║  ██║   ██║   
╚═╝     ╚═╝  ╚═╝╚═╝╚═════╝ ╚═╝  ╚═╝   ╚═╝   
```
A highly personalized, privacy focused AI assistant with a swarm of agents to make life easy. Orchestrates using the Plan-Execute-Reflect agentic loop, using ollama for inference and runs user interface on a Discord server using discord.py 

### Available agents:
- Web: runs google search using Google genai package
- Google Workspace (using API services):
  -  Tasks [CRUD tools complete]
  -  Sheets
  -  Calendar
  -  Drive [WIP]
---
### Voice Pipeline
ASR (Nvidia Parakeet 0.6B TDT v2) → Friday orchestrator agent → TTS (Kokoro 82M)

Speech modules [WIP] require ONNX runtime packages: ```onnx-asr``` & ```kokoro-onnx```

Reference links for setup:
  - https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx
  - https://github.com/thewh1teagle/kokoro-onnx
