"""Test harness: build ConversationSessions wired to real (in-memory) providers."""

from __future__ import annotations

from engine.engine import ActionEngine
from providers.internal import (
    InMemoryCustomerProvider,
    InMemoryNotificationProvider,
    InMemorySchedulingProvider,
    InternalDataset,
)
from providers.mock_external import MockExternalProvider, MockExternalSystem
from providers.registry import (
    MOCK_EXTERNAL,
    STEDDEN_INTERNAL,
    ProviderBundle,
    ProviderConfig,
    resolve_providers,
)
from seed.lumera import LUMERA_TZ, lumera_internal_dataset, lumera_kb
from simulator.session import ConversationSession
from tests.fakes import ScriptedGenerator, ScriptedTurn


def internal_session_with(
    generator: object, *, data: InternalDataset | None = None
) -> tuple[ConversationSession, InternalDataset]:
    """Build an internal-provider session around any generator (scripted, malformed…)."""
    data = data or lumera_internal_dataset()
    internal = ProviderBundle(
        customer=InMemoryCustomerProvider(data),
        scheduling=InMemorySchedulingProvider(data),
        notification=InMemoryNotificationProvider(data),
    )
    providers = resolve_providers(ProviderConfig(), bundles={STEDDEN_INTERNAL: internal})
    session = ConversationSession(
        lumera_kb(), generator, ActionEngine(), providers, call_id="sim-x"
    )
    return session, data


def make_internal_session(
    turns: list[ScriptedTurn], *, data: InternalDataset | None = None
) -> tuple[ConversationSession, InternalDataset]:
    return internal_session_with(ScriptedGenerator(turns), data=data)


def make_external_session(
    turns: list[ScriptedTurn], *, system: MockExternalSystem | None = None
) -> tuple[ConversationSession, MockExternalSystem]:
    system = system or MockExternalSystem(tz=LUMERA_TZ)
    provider = MockExternalProvider(system)
    bundle = ProviderBundle(customer=provider, scheduling=provider, notification=provider)
    config = ProviderConfig(
        customer_provider=MOCK_EXTERNAL,
        scheduling_provider=MOCK_EXTERNAL,
        notification_provider=MOCK_EXTERNAL,
    )
    providers = resolve_providers(config, bundles={MOCK_EXTERNAL: bundle})
    session = ConversationSession(
        lumera_kb(), ScriptedGenerator(turns), ActionEngine(), providers, call_id="sim-ext"
    )
    return session, system
