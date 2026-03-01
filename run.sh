#!/bin/bash
SITE=".venv/lib/python3.13/site-packages"
export LD_LIBRARY_PATH="\
$SITE/nvidia/cublas/lib:\
$SITE/nvidia/cuda_runtime/lib:\
$SITE/nvidia/cuda_nvrtc/lib:\
$SITE/nvidia/cudnn/lib:\
$SITE/nvidia/cufft/lib:\
$SITE/nvidia/curand/lib:\
$SITE/nvidia/nvjitlink/lib\
${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec .venv/bin/python3 discord_bot.py "$@"
