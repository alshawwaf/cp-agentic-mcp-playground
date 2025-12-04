FROM python:3.10-slim

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y \
    git \
    gcc \
    g++ \
    libomp-dev \
    && rm -rf /var/lib/apt/lists/*

# Install vLLM for CPU
# We use the official instruction to install from source or specific CPU wheels if available.
# Since building from source takes a long time, we try to find a compatible method.
# For now, we will build from source as it is the most reliable method for CPU support.

RUN pip install --upgrade pip

# Install vLLM with CPU support
# VLLM_TARGET_DEVICE=cpu tells the build system to build for CPU
ENV VLLM_TARGET_DEVICE=cpu

# We install directly from the repo to get the latest CPU support
# This might take 10-20 minutes to build!
RUN pip install vllm --no-binary vllm
RUN pip install git+https://github.com/huggingface/transformers.git

# Expose port
EXPOSE 8000

# Entrypoint
ENTRYPOINT ["python3", "-m", "vllm.entrypoints.openai.api_server"]
