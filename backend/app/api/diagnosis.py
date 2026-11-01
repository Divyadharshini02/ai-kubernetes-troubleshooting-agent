from fastapi import APIRouter

from app.api.evidence import NamespaceName, collect_or_http_error
from app.diagnostics.engine import diagnose
from app.models.diagnosis import DiagnosticReport

router = APIRouter(tags=["diagnosis"])


@router.get("/namespaces/{namespace}/diagnosis", response_model=DiagnosticReport)
def get_namespace_diagnosis(namespace: NamespaceName, include_evidence: bool = False) -> DiagnosticReport:
    """Collect live evidence for one namespace and evaluate the deterministic rules.

    Recommendations are suggestions for a person to review. Nothing here changes the
    cluster. Set include_evidence to true to receive the cited evidence in the response.
    """
    bundle = collect_or_http_error(namespace)
    report = diagnose(bundle)
    if include_evidence:
        report.evidence = bundle
    return report
