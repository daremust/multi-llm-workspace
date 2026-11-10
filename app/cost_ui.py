"""Shared cost presentation for generation and optional compute."""

from decimal import Decimal

import streamlit as st

from app.generation import Estimate


def money(value: Decimal | float | None) -> str:
    if value is None:
        return 'unknown'
    if value == 0:
        return '$0'
    return f'${value:.8f}'.rstrip('0').rstrip('.') if value >= Decimal('0.00000001') else '<$0.00000001'


UNKNOWN_TOTAL_WARNING = (
    'Total cost is unknown: pricing is missing or malformed, or additional fees or pricing tiers '
    'are unresolved. The base-rate token estimate excludes those adjustments. '
    'You can proceed, but it is not a spending cap.'
)


def estimate_summary(estimate: Estimate) -> str:
    return (f'Base-rate token estimate: {money(estimate.token_cost_usd)} · '
            f'Total cost: {money(estimate.cost_usd)}'
            + (' · Unresolved pricing; not a spending cap' if estimate.cost_usd is None else ''))


def render_estimate(estimate: Estimate) -> None:
    st.write(f'Base-rate token estimate: {money(estimate.token_cost_usd)}')
    st.write(f'Total cost: {money(estimate.cost_usd)}')
    if estimate.cost_usd is None:
        st.warning(UNKNOWN_TOTAL_WARNING)


