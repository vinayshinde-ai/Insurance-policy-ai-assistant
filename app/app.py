"""Streamlit UI for the Insurance Policy AI Assistant.

Author: Vinay Shinde, Generative AI Engineer (vinayshinde.tech@outlook.com)
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

import boto3
import streamlit as st
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from streamlit_cognito_auth import CognitoAuthenticator

from assistant import (
    MAX_QUESTION_CHARS, PASSWORD_RULES, Settings, StreamResult, auth_error_message, build_messages, citations,
    clean_question, format_grounding, iter_converse_stream, parse_retrieval_results, policy_key, system_prompt,
    validate_signup, NO_POLICY_TEXT,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("insurance-assistant")

GREETING = ("Hello! I'm Felix, your AnyCompany Insurance Policy AI Assistant. "
            "Ask me anything about your motor insurance policy.")

st.set_page_config(page_title="Insurance Policy AI Assistant", page_icon="🚗")


@st.cache_resource
def settings() -> Settings:
    return Settings.from_env()


@st.cache_resource
def clients():
    cfg = Config(connect_timeout=10, read_timeout=120, retries={"max_attempts": 4, "mode": "adaptive"})
    region = settings().region
    return {
        "runtime": boto3.client("bedrock-runtime", region_name=region, config=cfg),
        "agent": boto3.client("bedrock-agent-runtime", region_name=region, config=cfg),
        "s3": boto3.client("s3", region_name=region),
        "table": boto3.resource("dynamodb", region_name=region).Table(settings().chat_table),
    }


@st.cache_data(ttl=300, show_spinner=False)
def load_policy(username: str) -> Optional[str]:
    """Return the customer's policy text, or None if no policy exists for this user."""
    try:
        obj = clients()["s3"].get_object(Bucket=settings().policy_bucket, Key=policy_key(username))
        return obj["Body"].read().decode("utf-8")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            log.warning("No policy document for user=%s", username)
            return None
        raise


def save_turn(session_id: str, username: str, question: str, result: StreamResult, latency_ms: int) -> None:
    """Store one chat turn for analytics. Failure here must not break the chat."""
    now = time.time()
    try:
        clients()["table"].put_item(Item={
            "session_id": session_id,
            "ts": int(now * 1000),
            "username": username,
            "question": question,
            "answer": result.text,
            "stop_reason": result.stop_reason or "unknown",
            "guardrail_intervened": result.guardrail_intervened,
            "input_tokens": int(result.usage.get("inputTokens", 0)),
            "output_tokens": int(result.usage.get("outputTokens", 0)),
            "latency_ms": latency_ms,
            "expires_at": int(now + settings().chat_ttl_days * 86400),
        })
    except (ClientError, BotoCoreError):
        log.exception("Failed to save chat turn session=%s", session_id)


def answer(username: str, policy_text: str, question: str, history: list) -> StreamResult:
    cfg, c = settings(), clients()
    retrieved = c["agent"].retrieve(
        knowledgeBaseId=cfg.kb_id,
        retrievalQuery={"text": question},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": cfg.kb_results}},
    )
    sources = parse_retrieval_results(retrieved.get("retrievalResults", []))
    response = c["runtime"].converse_stream(
        modelId=cfg.model_id,
        system=[{"text": system_prompt(username, cfg.support_email)}],
        messages=build_messages(history, format_grounding(policy_text, sources, cfg.support_email), question,
                                cfg.history_turns),
        inferenceConfig={"maxTokens": cfg.max_tokens, "temperature": 0.2},
        guardrailConfig={
            "guardrailIdentifier": cfg.guardrail_id,
            "guardrailVersion": cfg.guardrail_version,
            "streamProcessingMode": "sync",  # evaluate before text reaches the user
            "trace": "enabled",  # needed to detect output blocks
        },
    )
    result = StreamResult()
    placeholder = st.empty()
    for _ in iter_converse_stream(response["stream"], result):
        placeholder.markdown(result.text + "▌")
    if not result.text.strip():
        result.text = f"Sorry, I couldn't produce an answer. Please rephrase, or email {cfg.support_email}."
    refs = citations(sources)
    if refs and not result.guardrail_intervened:
        result.text += "\n\n**Sources searched:** " + "; ".join(refs)
    placeholder.markdown(result.text)
    return result


# ---------------------------------------------------------------- authentication
@st.cache_resource
def cognito_client():
    return boto3.client("cognito-idp", region_name=settings().region)


def _error_code(e: ClientError) -> str:
    return e.response.get("Error", {}).get("Code", "")


def _set_auth_mode(mode: str) -> None:
    st.session_state.auth_mode = mode
    if mode == "signin":
        st.session_state.pop("signup_pending", None)


def show_signup() -> None:
    """Two steps: register with username and email, then confirm the code Cognito emails."""
    client, client_id = cognito_client(), settings().app_client_id
    st.title("Create your account")
    pending = st.session_state.get("signup_pending")

    if not pending:
        st.caption("Choose a username and enter your email. We'll send you a code to confirm it.")
        with st.form("signup_form"):
            username = st.text_input("Username").strip().lower()
            email = st.text_input("Email").strip()
            password = st.text_input("Password", type="password", help=f"Use {PASSWORD_RULES}.")
            confirm = st.text_input("Confirm password", type="password")
            submitted = st.form_submit_button("Create account")
        if submitted:
            error = validate_signup(username, email, password, confirm)
            if error:
                st.error(error)
            else:
                try:
                    client.sign_up(
                        ClientId=client_id, Username=username, Password=password,
                        UserAttributes=[{"Name": "email", "Value": email}],
                    )
                    log.info("signup requested user=%s", username)
                    st.session_state.signup_pending = username
                    st.rerun()
                except ClientError as e:
                    log.info("signup failed user=%s code=%s", username, _error_code(e))
                    st.error(auth_error_message(_error_code(e)))
        with st.expander("I already have a verification code"):
            existing = st.text_input("Username", key="pending_username").strip().lower()
            if st.button("Continue") and existing:
                st.session_state.signup_pending = existing
                st.rerun()
    else:
        st.info(f"We've emailed a verification code for **{pending}**. Enter it below to finish.")
        with st.form("confirm_form"):
            code = st.text_input("Verification code").strip()
            verified = st.form_submit_button("Verify")
        if verified:
            try:
                client.confirm_sign_up(ClientId=client_id, Username=pending, ConfirmationCode=code)
                log.info("signup confirmed user=%s", pending)
                _set_auth_mode("signin")
                st.session_state.auth_notice = "Your account is ready. Please sign in."
                st.rerun()
            except ClientError as e:
                st.error(auth_error_message(_error_code(e)))
        if st.button("Resend code"):
            try:
                client.resend_confirmation_code(ClientId=client_id, Username=pending)
                st.success("A new code is on its way.")
            except ClientError as e:
                st.error(auth_error_message(_error_code(e)))

    st.button("Back to sign in", on_click=_set_auth_mode, args=("signin",))


authenticator = CognitoAuthenticator(
    pool_id=settings().user_pool_id, app_client_id=settings().app_client_id, use_cookies=False,
)
st.session_state.setdefault("auth_mode", "signin")

if not authenticator.is_logged_in() and st.session_state.auth_mode == "signup":
    show_signup()
    st.stop()

notice = st.session_state.pop("auth_notice", None)
if notice:
    st.success(notice)
try:
    logged_in = authenticator.login()
except ClientError as e:
    # e.g. a new password that breaks the policy during the first-login password change
    log.info("login failed code=%s", _error_code(e))
    st.error(auth_error_message(_error_code(e)))
    logged_in = False

if not logged_in:
    st.caption(f"Passwords need {PASSWORD_RULES}.")
    st.button("Create a new account", on_click=_set_auth_mode, args=("signup",))
    st.stop()

username = authenticator.get_username() or ""


def logout() -> None:
    authenticator.logout()
    for key in ("messages", "session_id"):
        st.session_state.pop(key, None)


with st.sidebar:
    st.markdown(f"Signed in as **{username}**")
    st.button("Logout", key="logout_btn", on_click=logout)
    st.caption("Built by Vinay Shinde, Generative AI Engineer")

# ---------------------------------------------------------------- chat
st.title("Insurance Policy AI Assistant")

try:
    policy_text = load_policy(username)
except ValueError:
    st.error("Your account name is not valid for policy lookup. Please contact support.")
    st.stop()
except (ClientError, BotoCoreError):
    log.exception("Policy lookup failed for user=%s", username)
    st.error("We couldn't load your policy right now. Please try again shortly.")
    st.stop()

if policy_text is None:
    st.info("No personal policy is linked to your account yet, so I can answer general questions about "
            f"AnyCompany motor insurance. To link your policy, email {settings().support_email}.")
    policy_text = NO_POLICY_TEXT

st.session_state.setdefault("session_id", uuid.uuid4().hex)
st.session_state.setdefault("messages", [{"role": "assistant", "content": GREETING}])

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

raw = st.chat_input("Ask about your motor insurance policy", max_chars=MAX_QUESTION_CHARS)
if raw:
    question, error = clean_question(raw)
    if error:
        st.warning(error)
        st.stop()
    with st.chat_message("user"):
        st.markdown(question)
    history = list(st.session_state.messages)
    st.session_state.messages.append({"role": "user", "content": question})

    with st.chat_message("assistant"):
        started = time.time()
        try:
            with st.spinner("Checking your policy..."):
                result = answer(username, policy_text, question, history)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            log.error("Bedrock error code=%s session=%s", code, st.session_state.session_id)
            msg = ("We're handling a lot of requests right now. Please try again in a moment."
                   if code in ("ThrottlingException", "ServiceQuotaExceededException", "TooManyRequestsException")
                   else "Something went wrong while answering. Please try again.")
            result = StreamResult(text=msg, stop_reason=f"error:{code}")
            st.markdown(msg)
        except BotoCoreError:
            log.exception("AWS client error session=%s", st.session_state.session_id)
            result = StreamResult(text="Something went wrong while answering. Please try again.", stop_reason="error")
            st.markdown(result.text)
        latency = int((time.time() - started) * 1000)

    log.info("turn session=%s stop=%s guardrail=%s latency_ms=%d in=%s out=%s",
             st.session_state.session_id, result.stop_reason, result.guardrail_intervened, latency,
             result.usage.get("inputTokens"), result.usage.get("outputTokens"))
    st.session_state.messages.append({"role": "assistant", "content": result.text})
    save_turn(st.session_state.session_id, username, question, result, latency)
