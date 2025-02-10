import logging
import json
import time
from typing import Optional

import aiohttp
from aiocache import cached
import requests

from fastapi import Depends, FastAPI, HTTPException, Request, APIRouter
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from pydantic import BaseModel

from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.access_control import has_access

from open_webui.models.models import Models
from open_webui.env import ENV, SRC_LOG_LEVELS, BYPASS_MODEL_ACCESS_CONTROL, ENABLE_FORWARD_USER_INFO_HEADERS, AIOHTTP_CLIENT_TIMEOUT

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["AZURE"])

class AzureConfigForm(BaseModel):
    ENABLE_AZURE_API: Optional[bool] = None
    AZURE_API_BASE_URLS: list[str]
    AZURE_API_CONFIGS: dict
    AZURE_API_KEYS: list[str]

@cached(ttl=3)
async def get_all_models(request: Request) -> dict[str, list]:

    models = []
    configs = request.app.state.config.AZURE_API_CONFIGS
    for index in configs:
        for index, model in enumerate(configs[index]['model_ids']):
            models.append({
                "id": model,
                "name": model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "azure",
                "azure": {},
                "urlIdx": index
            })

    request.app.state.AZURE_MODELS = {model["id"]: model for model in models}

    return {"data":models}

async def cleanup_response(
    response: Optional[aiohttp.ClientResponse],
    session: Optional[aiohttp.ClientSession],
):
    if response:
        response.close()
    if session:
        await session.close()


##########################################
#
# API routes
#
##########################################

router = APIRouter()

@router.get("/config")
async def get_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_AZURE_API": request.app.state.config.ENABLE_AZURE_API,
        "AZURE_API_BASE_URLS": request.app.state.config.AZURE_API_BASE_URLS,
        "AZURE_API_CONFIGS": request.app.state.config.AZURE_API_CONFIGS,
        "AZURE_API_KEYS": request.app.state.config.AZURE_API_KEYS
    }

@router.post("/config/update")
async def update_config(
    request: Request, form_data: AzureConfigForm, user=Depends(get_admin_user)
):
    request.app.state.config.ENABLE_AZURE_API = form_data.ENABLE_AZURE_API
    request.app.state.config.AZURE_API_BASE_URLS = form_data.AZURE_API_BASE_URLS
    request.app.state.config.AZURE_API_CONFIGS = form_data.AZURE_API_CONFIGS
    request.app.state.config.AZURE_API_KEYS = form_data.AZURE_API_KEYS

    # Remove the API configs that are not in the API URLS
    keys = list(map(str, range(len(request.app.state.config.AZURE_API_BASE_URLS))))
    request.app.state.config.AZURE_API_CONFIGS = {
        key: value
        for key, value in request.app.state.config.AZURE_API_CONFIGS.items()
        if key in keys
    }

    return {
        "ENABLE_AZURE_API": request.app.state.config.ENABLE_AZURE_API,
        "AZURE_API_BASE_URLS": request.app.state.config.AZURE_API_BASE_URLS,
        "AZURE_API_CONFIGS": request.app.state.config.AZURE_API_CONFIGS,
        "AZURE_API_KEYS": request.app.state.config.AZURE_API_KEYS
    }

@router.get("/models")
@router.get("/models/{url_idx}")
async def get_models(
    request: Request, url_idx: Optional[int] = None, user=Depends(get_verified_user)
):
    models = []

    if url_idx is None:
        models = await get_all_models(request)
    else:
        if url_idx in request.app.state.config.AZURE_API_CONFIGS:
            config = request.app.state.config.AZURE_API_CONFIGS[url_idx]
            models = [
                {
                    "id": model,
                    "name": model,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "azure",
                    "azure": {},
                }
                for model in config.model_ids
            ]

    #if user.role == "user" and not BYPASS_MODEL_ACCESS_CONTROL:
    #    models["data"] = await get_filtered_models(models, user)

    return {"data": models}

@router.post("/chat/completions")
async def generate_chat_completion(
    request: Request,
    form_data: dict,
    user=Depends(get_verified_user),
    bypass_filter: Optional[bool] = False,
):
    if BYPASS_MODEL_ACCESS_CONTROL:
        bypass_filter = True

    idx = 0

    payload = {**form_data}
    metadata = payload.pop("metadata", None)

    model_id = form_data.get("model")
    model_info = Models.get_model_by_id(model_id)

    # Check model info and override the payload
    if model_info:
        if model_info.base_model_id:
            payload["model"] = model_info.base_model_id
            model_id = model_info.base_model_id

        params = model_info.params.model_dump()
        #payload = apply_model_params_to_body_openai(params, payload)
        #payload = apply_model_system_prompt_to_body(params, payload, metadata, user)

        # Check if user has access to the model
        if not bypass_filter and user.role == "user":
            if not (
                user.id == model_info.user_id
                or has_access(
                    user.id, type="read", access_control=model_info.access_control
                )
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Model not found",
                )
    elif not bypass_filter:
        if user.role != "admin":
            raise HTTPException(
                status_code=403,
                detail="Model not found",
            )

    await get_all_models(request)
    model = request.app.state.AZURE_MODELS.get(model_id)
    if model:
        idx = model["urlIdx"]
    else:
        raise HTTPException(
            status_code=404,
            detail="Model not found",
        )

    # Get the API config for the model
    api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
        str(idx),
        request.app.state.config.OPENAI_API_CONFIGS.get(
            request.app.state.config.OPENAI_API_BASE_URLS[idx], {}
        ),  # Legacy support
    )

    prefix_id = api_config.get("prefix_id", None)
    if prefix_id:
        payload["model"] = payload["model"].replace(f"{prefix_id}.", "")

    # Add user info to the payload if the model is a pipeline
    if "pipeline" in model and model.get("pipeline"):
        payload["user"] = {
            "name": user.name,
            "id": user.id,
            "email": user.email,
            "role": user.role,
        }

    url = request.app.state.config.AZURE_API_BASE_URLS[idx]
    key = request.app.state.config.AZURE_API_KEYS[idx]

    # Fix: O1 does not support the "max_tokens" parameter, Modify "max_tokens" to "max_completion_tokens"
    is_o1 = payload["model"].lower().startswith("o1-")
    if is_o1:
        payload = openai_o1_handler(payload)
    elif "api.openai.com" not in url:
        # Remove "max_completion_tokens" from the payload for backward compatibility
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload["max_completion_tokens"]
            del payload["max_completion_tokens"]

    if "max_tokens" in payload and "max_completion_tokens" in payload:
        del payload["max_tokens"]

    # Convert the modified body back to JSON
    payload = json.dumps(payload)

    r = None
    session = None
    streaming = False
    response = None

    try:
        session = aiohttp.ClientSession(
            trust_env=True, timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT)
        )

        r = await session.request(
            method="POST",
            url=f"{url}/chat/completions?api-version=2024-05-01-preview",
            data=payload,
            headers={
                "api-key": f"{key}",
                "Content-Type": "application/json"
            },
        )

        # Check if response is SSE
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            streaming = True
            return StreamingResponse(
                r.content,
                status_code=r.status,
                headers=dict(r.headers),
                background=BackgroundTask(
                    cleanup_response, response=r, session=session
                ),
            )
        else:
            try:
                response = await r.json()
            except Exception as e:
                log.error(e)
                response = await r.text()

            r.raise_for_status()
            return response
    except Exception as e:
        log.exception(e)

        detail = None
        if isinstance(response, dict):
            if "error" in response:
                detail = f"{response['error']['message'] if 'message' in response['error'] else response['error']}"
        elif isinstance(response, str):
            detail = response

        raise HTTPException(
            status_code=r.status if r else 500,
            detail=detail if detail else "Open WebUI: Server Connection Error",
        )
    finally:
        if not streaming and session:
            if r:
                r.close()
            await session.close()
