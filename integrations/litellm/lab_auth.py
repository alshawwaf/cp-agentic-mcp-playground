"""LiteLLM custom auth for the lab: accept LITELLM_MASTER_KEY, answer anything else with HTTP 401.

The lab runs LiteLLM without a database, so stock LiteLLM answers a wrong key with
HTTP 400 "No connected db.", which sends people looking for a database problem. This
check gives the honest answer: 401 and which key to use.

It also keeps rejected requests out of Langfuse. Stock LiteLLM records every refused
request (a probe of /v1/models with a wrong key, for example) as a failed trace with two
ERROR generations, which buries the agent runs trainees are looking for. A refused
request never reached a model, so it is logged by LiteLLM in the container log only.

LiteLLM imports this file from the directory of its config file. render_config.py copies
it there and sets `general_settings.custom_auth: lab_auth.user_api_key_auth`. It runs only
inside the LiteLLM image (it imports fastapi and litellm). The key is read from the
container environment on every request and is never logged.
"""
import hmac
import os

from fastapi import HTTPException, Request
from litellm.proxy._types import LitellmUserRoles, ProxyErrorTypes, UserAPIKeyAuth

# Stands in for the master key in LiteLLM's request metadata and callbacks (never the key itself).
LAB_KEY_ALIAS = "lab-master-key"
REJECTED = ("Invalid or missing LiteLLM key. Send the LITELLM_MASTER_KEY value from the lab .env "
            "as 'Authorization: Bearer <key>'.")


async def user_api_key_auth(request: Request, api_key: str) -> UserAPIKeyAuth:
    expected = os.environ.get("LITELLM_MASTER_KEY", "")
    given = api_key if isinstance(api_key, str) else ""
    if expected and given and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
        return UserAPIKeyAuth(api_key=LAB_KEY_ALIAS, key_alias=LAB_KEY_ALIAS, user_id="lab-admin",
                              user_role=LitellmUserRoles.PROXY_ADMIN)
    raise HTTPException(status_code=401, detail=REJECTED)


def _skip_auth_failures(original):
    def is_proxy_only_llm_api_error(self, *args, **kwargs):
        if kwargs.get("error_type") == ProxyErrorTypes.auth_error:
            return False  # refused at the door: no model call happened, so no trace
        return original(self, *args, **kwargs)
    return is_proxy_only_llm_api_error


try:  # pinned image: litellm 1.103.1. If the hook is renamed upstream this is a no-op.
    from litellm.proxy.utils import ProxyLogging

    _original = getattr(ProxyLogging, "_is_proxy_only_llm_api_error", None)
    if callable(_original):
        ProxyLogging._is_proxy_only_llm_api_error = _skip_auth_failures(_original)
except ImportError:
    pass
