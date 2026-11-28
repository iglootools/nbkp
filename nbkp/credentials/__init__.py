"""Credential retrieval for LUKS passphrases."""

from nbkp.credentials.passphrases import (
    CommandFn,
    CredentialError,
    CredentialErrorKind,
    PassphraseCache,
    PassphraseFn,
    PassphrasePrefetch,
    PromptFn,
    PromptNotSuppliedError,
    build_passphrase_fn,
    collect_passphrase_ids,
    is_prefetchable,
    prefetch_count,
    prefetch_passphrases,
    retrieve_passphrase,
    run_captured,
)

__all__ = [
    "CommandFn",
    "CredentialError",
    "CredentialErrorKind",
    "PassphraseCache",
    "PassphraseFn",
    "PassphrasePrefetch",
    "PromptFn",
    "PromptNotSuppliedError",
    "build_passphrase_fn",
    "collect_passphrase_ids",
    "is_prefetchable",
    "prefetch_count",
    "prefetch_passphrases",
    "retrieve_passphrase",
    "run_captured",
]
