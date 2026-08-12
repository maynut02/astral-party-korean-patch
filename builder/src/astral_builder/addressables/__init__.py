"""Unity Addressables catalog parsing and resolution."""

from astral_builder.addressables.catalog import (
    AddressablesCatalog,
    AssetBundleRequestOptions,
    CatalogLocation,
)
from astral_builder.addressables.resolver import (
    BundleOrigin,
    ResolvedBundle,
    ResolvedTarget,
    resolve_target,
)

__all__ = [
    "AddressablesCatalog",
    "AssetBundleRequestOptions",
    "BundleOrigin",
    "CatalogLocation",
    "ResolvedBundle",
    "ResolvedTarget",
    "resolve_target",
]
