"""Fixed, tool-free provider adapter with durable pre-HTTP budget admission."""

from uuid import UUID, uuid4

import httpx
import psycopg
from sqlalchemy.engine import make_url

from app.agents import ModelResponse, invoice_run, mandate_run


def postgres_dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


class ProviderUnavailable(ValueError):
    pass


class DurableDispatch:
    def __init__(self, *, run_id: UUID, database_url: str, api_key: str):
        self.run_id, self.dsn, self.api_key = str(run_id), postgres_dsn(database_url), api_key
        self.input_token_bound = 0

    def __call__(self, provider, model, messages, *, max_output_tokens=None):
        bound = self.input_token_bound
        if (provider, model, max_output_tokens) != ("openai", "gpt-6.1-sol", 1024):
            raise ProviderUnavailable("unreviewed provider request")
        if type(bound) is not int or not 0 < bound <= 272000 or not self.api_key:
            raise ProviderUnavailable("provider not configured or bound invalid")
        reserve = 2 * bound + 10 * max_output_tokens
        with psycopg.connect(self.dsn, connect_timeout=5) as connection:
            connection.execute(
                "INSERT INTO llm_budgets(run_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (self.run_id,),
            )
            admitted = connection.execute(
                """UPDATE llm_budgets SET requests=requests+1,
                    charged_micros=charged_micros+%s, halted=TRUE
                    WHERE run_id=%s AND NOT halted AND requests<4
                    AND charged_micros+%s<=1000000 RETURNING requests""",
                (reserve, self.run_id, reserve),
            ).fetchone()
        if not admitted:
            raise ProviderUnavailable("durable provider budget refused")
        # The committed charge/halt survives a crash or uncertain provider outcome.
        try:
            with httpx.Client(timeout=40, follow_redirects=False, trust_env=False) as client:
                response = client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": "Bearer " + self.api_key},
                    json={
                        "model": model,
                        "messages": messages,
                        "max_completion_tokens": max_output_tokens,
                        "store": False,
                        "service_tier": "default",
                    },
                )
                response.raise_for_status()
                body = response.json()
            choice, usage = body["choices"][0], body["usage"]
            message = choice["message"]
            if choice["finish_reason"] != "stop" or message.get("tool_calls"):
                raise ValueError("incomplete or tool-bearing response")
            tokens = (
                usage["prompt_tokens"],
                usage["completion_tokens"],
                usage.get("prompt_tokens_details", {}).get("cached_tokens", 0),
                usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
            )
            if (
                any(type(n) is not int or n < 0 for n in tokens)
                or not 0 < tokens[0] <= bound
                or tokens[1] > max_output_tokens
                or tokens[2] > tokens[0]
                or tokens[3] > tokens[1]
                or not isinstance(message.get("content"), str)
            ):
                raise ValueError("invalid usage")
            actual = 2 * tokens[0] + 10 * tokens[1]
            with psycopg.connect(self.dsn, connect_timeout=5) as connection:
                connection.execute(
                    """UPDATE llm_budgets SET charged_micros=charged_micros-%s,
                       halted=FALSE WHERE run_id=%s AND requests=%s AND halted""",
                    (reserve - actual, self.run_id, admitted[0]),
                )
            return ModelResponse(
                content=message["content"],
                input_tokens=tokens[0],
                output_tokens=tokens[1],
                cached_tokens=tokens[2],
                reasoning_tokens=tokens[3],
            )
        except Exception:
            raise ProviderUnavailable("provider request failed closed") from None


def governed_analysis(run_id: UUID, *, settings, action):
    """Run all initial invoice calls in one owning thread and one TokenOps scope."""
    dispatch = DurableDispatch(
        run_id=run_id,
        database_url=settings.database_url,
        api_key=settings.llm_api_key.get_secret_value() if settings.llm_api_key else "",
    )
    with invoice_run(run_id, dispatch=dispatch) as run:

        def complete(node, messages, *, input_token_bound):
            dispatch.input_token_bound = input_token_bound
            return run.complete(node, messages, input_token_bound=input_token_bound)

        return action(complete)


def mandate_completion(settings):
    def complete(node, messages, *, input_token_bound):
        run_id = uuid4()
        dispatch = DurableDispatch(
            run_id=run_id,
            database_url=settings.database_url,
            api_key=settings.llm_api_key.get_secret_value() if settings.llm_api_key else "",
        )
        dispatch.input_token_bound = input_token_bound
        with mandate_run(run_id, dispatch=dispatch) as run:
            return run.complete(node, messages, input_token_bound=input_token_bound)

    return complete
