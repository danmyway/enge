from typing import List, Optional
from .set_flow import RequestSpec


def build_tier_plan_specs(
    tiers: Optional[List[str]], plans: Optional[List[str]]
) -> List[RequestSpec]:
    specs: List[RequestSpec] = []
    if not tiers and not plans:
        return specs

    from enge.utils import opt_manager

    resolved_opts = opt_manager.parsed_opts
    source_spec = resolved_opts.source_spec
    target_spec = resolved_opts.target_spec
    upgrade_path = resolved_opts.upgrade_path_alias

    arches = resolved_opts.architectures or [""]

    if tiers:
        if plans:
            for tier in tiers:
                for plan in plans:
                    for arch in arches:
                        specs.append(
                            RequestSpec(
                                set_name=None,
                                tier=tier,
                                plan=plan,
                                arch=arch,
                                source_spec=source_spec,
                                target_spec=target_spec,
                                upgrade_path=upgrade_path,
                                effective_values={},
                            )
                        )
        else:
            for tier in tiers:
                for arch in arches:
                    specs.append(
                        RequestSpec(
                            set_name=None,
                            tier=tier,
                            plan=None,
                            arch=arch,
                            source_spec=source_spec,
                            target_spec=target_spec,
                            upgrade_path=upgrade_path,
                            effective_values={},
                        )
                    )
    else:
        for plan in plans or []:
            for arch in arches:
                specs.append(
                    RequestSpec(
                        set_name=None,
                        tier=None,
                        plan=plan,
                        arch=arch,
                        source_spec=source_spec,
                        target_spec=target_spec,
                        upgrade_path=upgrade_path,
                        effective_values={},
                    )
                )

    return specs
