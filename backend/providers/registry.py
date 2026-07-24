"""Resolve the source-of-truth providers for a business from its config.

One authoritative provider per capability (customer / scheduling / notification),
so we never double-write. This is a small resolver, not a plugin framework — new
providers are added by extending ``_BUNDLES`` / the match here.
"""

from __future__ import annotations

from dataclasses import dataclass

from providers.base import (
    CustomerProvider,
    NotificationProvider,
    ProviderNotConfigured,
    SchedulingProvider,
)

# Provider identifiers a business may configure.
STEDDEN_INTERNAL = "stedden_internal"
MOCK_EXTERNAL = "mock_external"


@dataclass(slots=True)
class ProviderConfig:
    """The per-business source-of-truth selection (persisted on the business)."""

    customer_provider: str = STEDDEN_INTERNAL
    scheduling_provider: str = STEDDEN_INTERNAL
    notification_provider: str = STEDDEN_INTERNAL


@dataclass(slots=True)
class ProviderBundle:
    """The three providers offered by one system (internal, or an external CRM)."""

    customer: CustomerProvider
    scheduling: SchedulingProvider
    notification: NotificationProvider


@dataclass(slots=True)
class ProviderSet:
    """The concrete providers the Action Engine will call for one business."""

    customer: CustomerProvider
    scheduling: SchedulingProvider
    notification: NotificationProvider
    config: ProviderConfig

    @property
    def scheduling_is_external(self) -> bool:
        return self.config.scheduling_provider != STEDDEN_INTERNAL


def resolve_providers(
    config: ProviderConfig,
    *,
    bundles: dict[str, ProviderBundle],
) -> ProviderSet:
    """Map a config to concrete providers, one authoritative source per capability.

    ``bundles`` maps a provider identifier (e.g. ``stedden_internal``,
    ``mock_external``) to the three providers that system offers. Capabilities are
    resolved independently, so a business could mix sources (external scheduling +
    internal notifications) without any special-casing.
    """

    def bundle(name: str, capability: str) -> ProviderBundle:
        found = bundles.get(name)
        if found is None:
            raise ProviderNotConfigured(f"provider '{name}' for {capability} is not available")
        return found

    return ProviderSet(
        customer=bundle(config.customer_provider, "customer").customer,
        scheduling=bundle(config.scheduling_provider, "scheduling").scheduling,
        notification=bundle(config.notification_provider, "notification").notification,
        config=config,
    )
