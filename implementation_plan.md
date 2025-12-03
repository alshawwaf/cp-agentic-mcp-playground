# vLLM CPU Deployment Plan

## Goal
Deploy vLLM on a CPU-only host using the fastest possible method (pre-built Docker image), avoiding the slow local build process.

## Problem
- The user's host has no GPU.
- `vllm/vllm-openai` (official Docker Hub image) requires GPU.
- `openvino/vllm-openvino` (guessed image) does not exist.
- Local build is too slow and complex to manage.

## Solution
Use the official vLLM CPU image hosted on AWS Public ECR:
`public.ecr.aws/q9t5s3a7/vllm-cpu-release-repo:v0.6.3.post1` (or latest stable)

## Proposed Changes

### 1. Update `docker-compose.yml`
- Change the default `image` for the `vllm` service to the AWS ECR image.
- Ensure `VLLM_TARGET_DEVICE=cpu` is set.

### 2. Update `.env-example`
- Reflect the new image source.

### 3. User Action
- User needs to update their `.env` file to point `VLLM_IMAGE` to the new AWS ECR image.

## Verification Plan
1.  **Pull Test**: Verify the image can be pulled without authentication.
2.  **Startup Test**: Start the service and check logs for successful CPU initialization.
