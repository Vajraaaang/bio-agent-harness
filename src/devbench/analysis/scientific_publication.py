"""Publication rendering and the public compatibility surface."""

from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass
from hashlib import sha256
from html import escape
from pathlib import Path

from devbench.core._files import hash_bounded_file
from devbench.core.publication_families import FamilyAllocation, synthesize_families
from devbench.engine.provenance import canonical_json

from ._publication_locations import (
    _LOCATION_COMMITTED as _LOCATION_COMMITTED,
)
from ._publication_locations import (
    _LOCATION_KEY as _LOCATION_KEY,
)
from ._publication_locations import (
    _LOCATION_LOCK as _LOCATION_LOCK,
)
from ._publication_locations import (
    PublicationGuard as PublicationGuard,
)
from ._publication_locations import (
    _known_adaptive_identity as _known_adaptive_identity,
)
from ._publication_locations import (
    _owned_location_path as _owned_location_path,
)
from ._publication_locations import (
    _publication_location_lock as _publication_location_lock,
)
from ._publication_locations import (
    _publication_location_record as _publication_location_record,
)
from ._publication_locations import (
    _reject_other_visible_ledger_identity as _reject_other_visible_ledger_identity,
)
from ._publication_locations import (
    _visible_publication_ledgers as _visible_publication_ledgers,
)
from ._publication_locations import (
    project_subject_metadata as project_subject_metadata,
)
from ._publication_locations import (
    publication_descriptor_for_source as publication_descriptor_for_source,
)
from ._publication_locations import (
    publication_service_settings as publication_service_settings,
)
from ._publication_locations import (
    record_publication_authority as record_publication_authority,
)
from ._publication_locations import (
    recorded_publication_ledgers as recorded_publication_ledgers,
)
from ._publication_locations import (
    workspace_guard as workspace_guard,
)
from ._publication_policy import (
    _CLAIMS as _CLAIMS,
)
from ._publication_policy import (
    _DIAGNOSTIC_CODES as _DIAGNOSTIC_CODES,
)
from ._publication_policy import (
    _EFFECTS as _EFFECTS,
)
from ._publication_policy import (
    _ERROR_FIELDS as _ERROR_FIELDS,
)
from ._publication_policy import (
    _OPAQUE as _OPAQUE,
)
from ._publication_policy import (
    _SHA as _SHA,
)
from ._publication_policy import (
    _STAT_EXTRA as _STAT_EXTRA,
)
from ._publication_policy import (
    AuditReplayEntitlement as AuditReplayEntitlement,
)
from ._publication_policy import (
    CheckedObservation as CheckedObservation,
)
from ._publication_policy import (
    PublicationAccess as PublicationAccess,
)
from ._publication_policy import (
    PublicationDescriptor as PublicationDescriptor,
)
from ._publication_policy import (
    PublicationSubject as PublicationSubject,
)
from ._publication_policy import (
    ReviewEligibility as ReviewEligibility,
)
from ._publication_policy import (
    ReviewState as ReviewState,
)
from ._publication_policy import (
    _digest as _digest,
)
from ._publication_policy import (
    _Frozen as _Frozen,
)
from ._publication_policy import (
    _key as _key,
)
from ._publication_policy import (
    _label as _label,
)
from ._publication_policy import (
    _same_transaction as _same_transaction,
)
from ._publication_policy import (
    access_from_delivery as access_from_delivery,
)
from ._publication_policy import (
    adaptive_publication_descriptor as adaptive_publication_descriptor,
)
from ._publication_policy import (
    audit_replay_entitlement as audit_replay_entitlement,
)
from ._publication_policy import (
    build_publication_descriptor as build_publication_descriptor,
)
from ._publication_policy import (
    is_statistic_field as is_statistic_field,
)
from ._publication_policy import (
    public_metadata as public_metadata,
)
from ._publication_policy import (
    safe_diagnostic as safe_diagnostic,
)
from ._publication_registry import (
    PublicationRegistry as PublicationRegistry,
)


def guard_publication_payload(registry, value, delivery, connection):
    """Recheck exact mixed JSON projections at reservation and attempted release."""
    if type(registry) is not PublicationRegistry:
        return
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if item.get("format") == "devbench.adaptive-publication/1":
                current = registry.projection(
                    item.get("subject_id"), delivery=delivery, connection=connection
                )
                if canonical_json(current) != canonical_json(item):
                    raise PermissionError("publication_changed_before_release")
            elif isinstance(item.get("source"), dict) and item["source"].get(
                "publication_subject_id"
            ):
                source = item["source"]
                subject, _ = registry._subject(
                    connection, source["publication_subject_id"]
                )
                descriptor = registry.descriptor(
                    subject.subject_id, delivery=delivery, connection=connection
                )
                registry._read(subject.source_path, subject.source_sha256)
                from .bulk_plot_service import _public_geometry_hash

                if (
                    not descriptor.allow_descriptive
                    or source.get("sha256") != subject.source_sha256
                    or source.get("publication_geometry_sha256")
                    != _public_geometry_hash(item)
                ):
                    raise PermissionError("publication_changed_before_release")
            else:
                pending.extend(item.values())
        elif isinstance(item, (tuple, list)):
            pending.extend(item)


def _retained_table(raw, *, source_path=None, guard_companion=None):
    """Project only registered retained result schemas."""
    from .scientific_result_contract import (
        ScientificResult,
        load_retained_result_manifest,
    )

    document = json.loads(raw)
    binary = document.get("record_format") == "devbench.scientific-result-manifest/1"
    if binary and (source_path is None or guard_companion is None):
        raise PermissionError(
            "Publication binary companion requires trusted local custody"
        )
    if (
        binary
        or document.get("record_format") == "devbench.scientific-result-diagnostic/1"
    ):
        result = (
            load_retained_result_manifest(
                raw, source_path, guard_companion=guard_companion
            )
            if binary
            else ScientificResult.model_validate_json(raw)
        )
        return {
            "schema": "scientific_result",
            "family_id": result.family.family_id,
            **({"binary_transport": True} if binary else {}),
            "effect_scale": result.method.effect_scale,
            "complete": result.family.complete,
            "eligible": len(result.family.eligible_ids),
            "failed": sum(
                row.status in {"fit_failed", "not_estimable", "not_tested"}
                for row in result.rows
            ),
            "rows": [
                {"gene_id": row.feature_id, "status": row.status, "effect": row.effect}
                for row in result.rows
            ],
        }
    if (
        type(document.get("target_results")) is list
        and len(document["target_results"]) == 1
        and document["target_results"][0].get("payload", {}).get("kind") == "effects"
    ):
        from .bulk_plot_data import build_bulk_plot_workspace

        workspace = build_bulk_plot_workspace(
            document,
            source={
                "sha256": sha256(raw).hexdigest(),
                "run_id": "retained-publication",
                "kind": "retained",
            },
        )
        if not workspace["genes"]:
            raise ValueError(
                "Publication source requires its full retained feature inventory"
            )
        return {
            "schema": "saved_bulk_result",
            "family_id": document["target_results"][0]["target_id"],
            "effect_scale": workspace["effect_scale"],
            "complete": False,
            "eligible": None,
            "failed": sum(
                row["estimate_status"] == "failed" for row in workspace["genes"]
            ),
            "rows": [
                {
                    "gene_id": row["gene_id"],
                    "status": row["estimate_status"],
                    "effect": row["effect"],
                }
                for row in workspace["genes"]
            ],
        }
    raise ValueError("publication_source_schema_unavailable")


@dataclass(frozen=True)
class RenderedPublication:
    """Trusted renderer-bound bytes; field-name assertions cannot certify a binary."""

    registry: PublicationRegistry
    delivery: object
    subject_id: str
    descriptor_json: str
    projection_bytes: bytes
    format: str
    presentation_request_bytes: bytes | None
    renderer_sha256: str
    content: bytes

    def release(self):
        return self.release_with_receipt()[0]

    def release_with_receipt(self):
        """Return exact delivered bytes and its actual current release witness."""
        broker = self.delivery.broker
        reservation = broker.reserve(
            self.delivery.context_id,
            self.delivery.source_id,
            self.content,
            _publication=self,
            _guard=self.guard,
        )
        broker.mark_attempted(reservation["reservation_id"], _guard=self.guard)
        return self.content, broker.reservation(reservation["reservation_id"])

    def guard(self, db):
        if _publication_renderer_sha256() != self.renderer_sha256:
            raise PermissionError("publication_renderer_binding_mismatch")
        descriptor = self.registry.descriptor(
            self.subject_id, delivery=self.delivery, connection=db
        )
        projection = self.registry.projection(
            self.subject_id,
            delivery=self.delivery,
            connection=db,
            presentation_request=json.loads(self.presentation_request_bytes)
            if self.presentation_request_bytes is not None
            else None,
        )
        if (
            descriptor.model_dump_json() != self.descriptor_json
            or canonical_json(projection) != self.projection_bytes
        ):
            raise PermissionError("publication_changed_before_release")

    def verify(self, broker, context_id, source_id, raw, db):
        if (
            type(self.registry) is not PublicationRegistry
            or self.delivery.broker is not broker
            or (self.delivery.context_id, self.delivery.source_id)
            != (context_id, source_id)
            or raw != self.content
            or raw
            != _render_publication(json.loads(self.projection_bytes), self.format)
        ):
            raise PermissionError("publication_renderer_binding_mismatch")
        self.guard(db)
        return json.loads(self.projection_bytes), {
            "subject_sha256": self.subject_id,
            "projection_sha256": sha256(self.projection_bytes).hexdigest(),
            "renderer": "adaptive_descriptive_v1:" + self.format,
            "renderer_sha256": self.renderer_sha256,
            "content_sha256": sha256(raw).hexdigest(),
            "content_bytes": len(raw),
        }


def _publication_renderer_sha256():
    directory = Path(__file__).resolve().parent
    return _digest(
        {
            name: hash_bounded_file(directory / name, 64 * 1024**2)
            for name in (
                "scientific_publication.py",
                "_publication_policy.py",
                "_publication_locations.py",
                "_publication_registry.py",
                "bulk_plot_service.py",
                "bulk_plot_data.py",
                "scientific_plot_artifacts.py",
            )
        }
    )


_FIGURE_FORMATS = ("svg", "png", "pdf")


def _render_publication_figures(projection, formats):
    presentation = projection.get("presentation")
    if presentation is not None:
        from .bulk_plot_service import audit_public_scientific_workspace
        from .scientific_plot_artifacts import _render_scientific_plots

        workspace, request = presentation["workspace"], presentation["request"]
        audit_public_scientific_workspace(workspace, request, current_use=False)
        return _render_scientific_plots(workspace, request, formats=formats)

    figure = projection.get("figure")
    if figure is None:
        raise PermissionError("publication_format_unavailable")
    exports = {"svg": figure["svg"].encode()} if "svg" in formats else {}
    plot_formats = tuple(format for format in formats if format != "svg")
    if not plot_formats:
        return exports

    import matplotlib
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from .scientific_plot_artifacts import _LOCK

    legacy_order = figure["renderer"] in {
        "adaptive_effect_points_v1",
        "fixed_effect_points_v1",
    }
    points, _, order = _effect_points(projection["table"], legacy_order=legacy_order)
    with (
        _LOCK,
        matplotlib.rc_context(
            {"text.usetex": False, "text.parse_math": False, "pdf.fonttype": 42}
        ),
    ):
        plot = Figure(figsize=(6, max(2, min(15, len(points) * 0.18))), dpi=100)
        FigureCanvasAgg(plot)
        try:
            axes = plot.subplots()
            axes.scatter(
                [row["effect"] for row in points],
                list(range(len(points))),
                color="#285b75",
                s=12,
            )
            axes.set_yticks(
                list(range(len(points))), [row["gene_id"] for row in points]
            )
            axes.set_xlabel(
                "Descriptive effect ("
                + projection.get("effect_scale", "unspecified")
                + ")"
            )
            axes.set_title(
                "Feature identifier order" if legacy_order else "Ordered by " + order
            )
            if not legacy_order:
                axes.invert_yaxis()
            plot.tight_layout()
            for format in plot_formats:
                output = io.BytesIO()
                metadata = (
                    {
                        "Creator": "DevBench adaptive descriptive renderer",
                        "CreationDate": None,
                        "ModDate": None,
                    }
                    if format == "pdf"
                    else {"Software": "DevBench adaptive descriptive renderer"}
                )
                plot.savefig(output, format=format, metadata=metadata)
                exports[format] = output.getvalue()
        finally:
            plot.clear()
    return exports


def _render_publication(projection, format):
    if format == "json":
        return canonical_json(projection)
    if format == "summary":
        text = "# RNA analysis publication\n\n"
        text += (
            "Retained family entries: "
            + str(len(projection.get("family_inventory", ())))
            + ".\n\n"
        )
        if projection.get("table_view", {}).get("status") == "preview":
            view = projection["table_view"]
            text += (
                f"Showing {view['shown_rows']} of "
                f"{view['total_rows']} retained rows.\n\n"
            )
        interpretation = projection.get("interpretation")
        text += (
            interpretation["text"]
            if interpretation is not None
            else "Interpretation is unavailable for this current use."
        ) + "\n"
        return text.encode()
    if format == "csv":
        stream = io.StringIO(newline="")
        fields = ["family_id", "gene_id", "status"]
        if any("effect" in row for row in projection.get("table", ())):
            fields.append("effect")
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(projection.get("table", ()))
        return stream.getvalue().encode()
    if format in _FIGURE_FORMATS:
        return _render_publication_figures(projection, (format,))[format]
    if format == "zip" and (
        projection.get("figure") is not None
        or projection.get("presentation") is not None
    ):
        import zipfile

        figures = _render_publication_figures(projection, _FIGURE_FORMATS)
        members = {
            "publication.json": _render_publication(projection, "json"),
            "table.csv": _render_publication(projection, "csv"),
            **{"figure." + kind: raw for kind, raw in figures.items()},
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in members.items():
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, raw)
        content = output.getvalue()
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if set(archive.namelist()) != set(members) or any(
                archive.read(name) != raw for name, raw in members.items()
            ):
                raise PermissionError("publication_archive_mismatch")
        return content
    raise PermissionError("publication_format_unavailable")


def family_accounting(allocation, evidence=(), *, shared_holds=None):
    """Exact T07 arithmetic retained for numerical audit and typed publication."""
    return synthesize_families(
        FamilyAllocation.model_validate(allocation), evidence, shared_holds=shared_holds
    )


def _effect_probability(row):
    q_value = row.get("q_budget")
    if q_value is None:
        q_value = row.get("q_value")
    return math.inf if q_value is None else q_value


def _effect_points(rows, *, legacy_order=False):
    eligible = [
        row
        for row in rows
        if row.get("effect") is not None and row["gene_id"] is not None
    ]
    if not legacy_order:
        # Only statistics admitted to the public table can influence the figure.
        eligible.sort(
            key=lambda row: (
                _effect_probability(row),
                -abs(row["effect"]),
                row["gene_id"],
                row.get("family_id") or "",
            )
        )
    points = eligible[:100]
    order = (
        "adjusted p-value and absolute effect"
        if points and _effect_probability(points[0]) < math.inf
        else "absolute effect"
    )
    return points, len(eligible), order


def _effect_figure(rows, source_sha256, *, adaptive_scale=None, legacy_order=False):
    points, eligible_count, order = _effect_points(rows, legacy_order=legacy_order)
    if not points:
        return None
    scale = max(1.0, *(abs(row["effect"]) for row in points))
    marks = []
    for index, row in enumerate(points):
        x = 160 + 145 * (row["effect"] / scale)
        y = 15 + index * 9
        label = escape(row["gene_id"], quote=True)
        marks.append(
            f'<circle cx="{x:.4f}" cy="{y}" r="3" fill="#285b75"><title>{label} '
            f"{row['effect']}</title></circle>"
        )
    if legacy_order:
        title = (
            "Descriptive effects (" + adaptive_scale + ") in feature identifier order"
            if adaptive_scale is not None
            else "Descriptive log2 fold changes in retained feature order"
        )
    else:
        effect_label = (
            adaptive_scale if adaptive_scale is not None else "log2 fold change"
        )
        title = f"Descriptive effects ({effect_label}) ordered by {order}"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 320 {len(points) * 9 + 25}">'
        "<title>" + escape(title) + "</title>" + "".join(marks) + "</svg>"
    )
    kind = "adaptive" if adaptive_scale is not None else "fixed"
    return {
        "format": f"devbench.{kind}-effect-figure/1",
        "renderer": f"{kind}_effect_points_v{1 if legacy_order else 2}",
        "table_sha256": _digest(rows),
        "source_sha256": source_sha256,
        "svg": svg,
        "mark_count": len(marks),
        "eligible_mark_count": eligible_count,
    }


def fixed_capsule(
    descriptor, allocation, evidence=(), *, shared_holds=None, _legacy_figure=False
):
    """Produce a closed, portable fixed numerical presentation without raw evidence."""
    descriptor = PublicationDescriptor.model_validate(descriptor)
    if (
        descriptor.scope != "fixed_capsule"
        or descriptor.allocation_sha256 != allocation.sha256()
    ):
        raise ValueError(
            "Fixed capsule publication basis differs from the allocated family"
        )
    inventory, table = [], []
    accounted = family_accounting(allocation, evidence, shared_holds=shared_holds)
    visible = descriptor.allow_descriptive
    available = "publication_source_withheld" not in descriptor.reasons
    for family in accounted["families"]:
        status = (
            family["status"] if visible or family["status"] != "ready" else "withheld"
        )
        reason = family["reason"]
        if family["family_id"] in descriptor.family_review_holds:
            status, reason = "withheld", "review_family_held"
        inventory.append(
            {
                "family_id": _label(family["family_id"]) if available else None,
                "status": status if available else "withheld",
                "weight": family["weight"],
                "unused_weight": family["unused_weight"]
                if status == family["status"]
                else family["weight"],
                **{
                    key: _label(family[key]) if available else None
                    for key in ("population", "numerator", "denominator")
                },
                "reason": safe_diagnostic(reason)
                if reason
                else ("publication_source_withheld" if status == "withheld" else None),
                "counts": family["counts"] if available else None,
            }
        )
        if not available:
            continue
        observed = {row["gene_id"]: row for row in family["genes"]}
        for feature_id in family["feature_ids"]:
            row = observed.get(feature_id, {})
            record = {
                "family_id": _label(family["family_id"]),
                "gene_id": _label(feature_id),
                "status": row.get("status", "withheld") if visible else "withheld",
            }
            if visible:
                record["effect"] = row.get("effect")
            if descriptor.allow_statistics and status == "ready":
                record.update(
                    {
                        key: row.get(key)
                        for key in (
                            "p",
                            "q_local",
                            "q_budget",
                            "passes_budget_threshold",
                        )
                    }
                )
            table.append(record)
    figure = (
        _effect_figure(table, descriptor.source_sha256, legacy_order=_legacy_figure)
        if visible
        else None
    )
    result = {
        "format": "devbench.fixed-publication/1",
        "scope": "fixed_capsule",
        "descriptor": descriptor.model_dump(mode="json"),
        "family_inventory": inventory,
        "table": table,
        "effect_scale": "log2_fold_change",
        "figure": figure,
        "interpretation": None,
    }
    return {**result, "publication_sha256": _digest(result)}


def _uses_legacy_fixed_figure(figure):
    return type(figure) is dict and figure.get("renderer") == "fixed_effect_points_v1"


def verify_fixed_publication(publication, expected_sha256):
    body = {
        key: value for key, value in publication.items() if key != "publication_sha256"
    }
    if (
        type(expected_sha256) is not str
        or _SHA.fullmatch(expected_sha256) is None
        or publication.get("format") != "devbench.fixed-publication/1"
        or publication.get("publication_sha256") != expected_sha256
        or _digest(body) != expected_sha256
    ):
        raise ValueError("Fixed publication digest mismatch")
    descriptor = PublicationDescriptor.model_validate_json(
        canonical_json(publication["descriptor"])
    )
    figure = publication["figure"]
    expected_figure = (
        _effect_figure(
            publication["table"],
            descriptor.source_sha256,
            legacy_order=_uses_legacy_fixed_figure(figure),
        )
        if descriptor.allow_descriptive
        else None
    )
    if figure != expected_figure:
        raise ValueError(
            "Fixed publication figure differs from its registered renderer and table"
        )
    return descriptor


def fixed_csv(publication):
    descriptor = verify_fixed_publication(
        publication, publication.get("publication_sha256")
    )
    fields = ["family_id", "gene_id", "status"]
    if descriptor.allow_descriptive:
        fields.append("effect")
    if descriptor.allow_statistics:
        fields += ["p", "q_local", "q_budget", "passes_budget_threshold"]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in publication["table"]:
        for key in ("family_id", "gene_id"):
            if row[key] is not None and _label(row[key]) != row[key]:
                raise ValueError("Unregistered publication table label")
        writer.writerow(row)
    return stream.getvalue().encode()
