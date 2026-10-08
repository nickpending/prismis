---
type: design
date: 2026-10-08
title: "Design: self-contained-llm-config"
description: "A new user goes from install to a working daemon by setting one environment variable, prismis carries no foreign config files or git-only key library, and the operator still keeps every key in one store that feeds prismis through the environment."
purpose: "The recorded design of the self-contained-llm-config work order, read by its plan and its builders."
producer: cli:shape
---

# Design: self-contained-llm-config

## Purpose

A new user goes from install to a working daemon by setting one environment variable, prismis carries no foreign config files or git-only key library, and the operator still keeps every key in one store that feeds prismis through the environment.

## Form

1. prismis owns its services. config.toml gains [services.<name>] tables: base_url, model, api_key, and the optional adapter ("openai" default, "decisions" for the kind classifier's endpoint), app_title and app_url the current schema carries. A service with no api_key is keyless (local servers), replacing key_required = false. [llm] light_service, deep_service and kind_service name these tables exactly as today. config.py (which already expands env:NAME for Reddit, line 250) parses the tables once; llm_client.py's resolve_service (line 108) resolves from that parsed config instead of reading ~/.config/llm-core/services.toml, and _config_dir with its LLM_CORE_CONFIG_DIR lookup goes.

2. Keys are environment variables. api_key is always `env:NAME`; load_api_key (line 134) reads os.environ[NAME], and a missing variable is a ConfigError naming the service and the variable. A literal value in a service's api_key is refused with the same kind of error, so no LLM provider key ever lives in a config file. prismis's own generated REST key ([api] key, [remote] key) is not a provider key and stays where it is. apiconf leaves daemon/pyproject.toml and llm_client.py. The existing startup load of ~/.config/prismis/.env (__main__.py:364) stays the newcomer's key file, and a variable already in the environment wins over it.

3. A first run that works. defaults.py's config defines one service, [services.openrouter] with base_url https://openrouter.ai/api/v1, model openai/gpt-5.4-nano and api_key env:OPENROUTER_API_KEY, the configuration production and every LLM test recording run, and light_service names it. The .env template (Makefile install-config), the first-run next-steps text and the README Quick Start all say: set OPENROUTER_API_KEY, run verify. verify names the config.toml service and the variable when a key is missing or a service is undefined, instead of pointing at make install-config or services.toml.

4. Existing installs migrate. migrate-config, when [llm] names a service that config.toml does not define, reads ~/.config/llm-core/services.toml read-only, takes only the entries [llm] names, and appends them to config.toml as [services.<name>] (default_model becomes model; key_required = false becomes no api_key; otherwise api_key = env:<VAR>, where VAR comes from the `provider` field of the matching [keys.<key>] entry in ~/.config/apiconf/config.toml, read as plain TOML for that field only, mapped the way apiconf's own provider table does: openai OPENAI_API_KEY, else PROVIDER_API_KEY). It never reads or writes a key value, backs config.toml up to a timestamped copy before appending, appends rather than rewriting so comments survive, and is idempotent. The older pre-llm-core [llm] provider format migrates straight to a [services.prismis-<provider>] table the same way, replacing today's path that writes services.toml and copies the key value into apiconf's file (__main__.py:625).

5. The operator's single key store sits outside prismis: the apiconf CLI's `apiconf env prismis` exports the variables (OPENROUTER_API_KEY on cerebro) at launch, in his shell and in cerebro's service unit, set up at deploy outside the lane.

## Commitments

- From an empty config home, the first run, then setting OPENROUTER_API_KEY, then `prismis-daemon verify` reaches a passing LLM check; the test runs this path against the local LLM stub with only base_url pointed at the stub.
- No prismis code path reads ~/.config/llm-core/services.toml except migrate-config, and apiconf is absent from daemon/pyproject.toml, uv.lock and every import.
- Every LLM provider key comes from an environment variable named by `env:NAME` in a [services.*] api_key; a literal api_key value and a missing variable each fail with an error naming the service and the variable. prismis's own [api]/[remote] REST key is unchanged.
- A service with no api_key works keyless.
- migrate-config on cerebro's current config produces [services.prismis-light], [services.prismis-deep] and [services.prismis-kind] in config.toml with api_key = "env:OPENROUTER_API_KEY", a timestamped backup of the previous file, no key value in any file it writes, and a second run that changes nothing.
- README Quick Start, the .env template and the first-run text name OPENROUTER_API_KEY and match what the code reads.
- After deploy, cerebro's daemon gets OPENROUTER_API_KEY from `apiconf env prismis` at launch, verify passes, and the first fetch cycle completes.

## Sacrifices

- prismis no longer follows edits to services.toml; its services live in its own config.toml after migration, and the prismis-* entries left in services.toml are inert for prismis.
- Keys no longer come from apiconf directly; the operator's store reaches prismis only through the environment, so the apiconf CLI and an app profile are needed on each host that runs the daemon.
- A newcomer default on OpenRouter means an OpenAI-only user edits base_url and model (documented in the README) rather than getting OpenAI by default.

## Risk

If cerebro restarts on the new build before OPENROUTER_API_KEY reaches the service's environment, every LLM call fails at the per-item boundary until it does; the deploy runs migrate-config and sets up the apiconf bridge before the restart, and verify after restart is the check. A services.toml entry with fields this schema does not carry would be dropped by migrate-config; the migration copies the known fields and reports any it left behind.

## Licensed by

daemon/src/prismis_daemon/config.py:250
