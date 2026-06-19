from typing import List, Optional
from .set_flow import RequestSpec


def build_tier_plan_specs(
    tiers: Optional[List[str]], plans: Optional[List[str]], ctx
) -> List[RequestSpec]:
    specs: List[RequestSpec] = []
    if not tiers and not plans:
        return specs

    source_spec = ctx.source_spec
    target_spec = ctx.target_spec
    upgrade_path = ctx.upgrade_path_alias

    arches = ctx.architectures or [""]

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
