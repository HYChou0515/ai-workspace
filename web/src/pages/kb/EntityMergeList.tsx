/**
 * #534 B — the merge queue.
 *
 * A row asks one question: are these two names the same thing? The answer has to
 * come from the documents, not from the model that raised it. Measured against a
 * real local model roughly half its groupings were wrong, and asking it to
 * justify them only made the wrong ones read better — it merged an inspection
 * machine with a printing machine and explained "a machine used for printing
 * solder paste", a sentence that is true of one of them.
 *
 * So each side leads with its own documents and the words around it, and the
 * model's reason is present but labelled as the model's. A reviewer weighs it;
 * they do not take it as the finding.
 */
import { useT } from "../../lib/i18n";
import { docHref } from "./kbLinks";

export type MergeEvidence = {
  source_doc_id: string;
  surface: string;
  text: string;
};

export type MergeProposal = {
  entity_id: string;
  other_id: string;
  name: string;
  other_name: string;
  why: string;
  evidence: MergeEvidence[];
  other_evidence: MergeEvidence[];
};

type Props = {
  proposals: MergeProposal[];
  onAccept: (entityId: string, otherId: string) => void;
  onReject: (entityId: string, otherId: string) => void;
};

function Side({ name, evidence }: { name: string; evidence: MergeEvidence[] }) {
  const t = useT();
  return (
    <div className="mrg__side">
      <div className="mrg__name">{name}</div>
      {evidence.length === 0 ? (
        <p className="mrg__none" data-testid="merge-no-evidence">
          {t("merge.noEvidence")}
        </p>
      ) : (
        <ul className="mrg__ev">
          {evidence.map((e) => (
            <li key={`${e.source_doc_id}:${e.text}`}>
              {/* An excerpt settles an obvious mismatch and not a close call, so
                  the document has to be one click away — opened AT the sentence,
                  and in a new tab so the reviewer keeps their place in the queue. */}
              <a
                className="mrg__doc"
                href={docHref(e.source_doc_id, e.text || e.surface)}
                target="_blank"
                rel="noreferrer"
              >
                {e.source_doc_id}
              </a>
              <span className="mrg__quote">{e.text || e.surface}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function EntityMergeList({ proposals, onAccept, onReject }: Props) {
  const t = useT();
  if (proposals.length === 0) {
    return (
      <p className="rvw__empty" data-testid="merge-empty">
        {t("merge.empty")}
      </p>
    );
  }
  return (
    <ul className="mrg">
      {proposals.map((p) => (
        <li className="mrg__row" key={`${p.entity_id}:${p.other_id}`}>
          <div className="mrg__pair">
            <Side name={p.name} evidence={p.evidence} />
            <div className="mrg__vs" aria-hidden="true">
              ≟
            </div>
            <Side name={p.other_name} evidence={p.other_evidence} />
          </div>
          <p className="mrg__why">
            <span className="mrg__whyLabel" data-testid="merge-why-label">
              {t("merge.whyLabel")}
            </span>
            <span data-testid="merge-why">{p.why}</span>
          </p>
          <div className="mrg__actions">
            <button
              type="button"
              className="btn"
              // Deliberately NOT the primary button. Measured against a real
              // model roughly half its groupings were wrong, so weighting
              // "same thing" would put the interface's thumb on the scale in
              // favour of the suggestion at the one moment a person is meant to
              // doubt it. Both answers are one click and neither is the default.
              data-variant="secondary"
              data-size="sm"
              onClick={() => onAccept(p.entity_id, p.other_id)}
            >
              {t("merge.same")}
            </button>
            <button
              type="button"
              className="btn"
              data-variant="secondary"
              data-size="sm"
              onClick={() => onReject(p.entity_id, p.other_id)}
            >
              {t("merge.different")}
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}
