/**
 * Containment for a view kind's renderer (#698).
 *
 * This seam exists so people who are not the platform team can ship a component
 * into this app. React has no other containment: without a boundary, one throw
 * anywhere below `createRoot` unmounts the whole tree, so a bad plug-in — or a
 * hostile `.ai.yaml` reaching a renderer that trusted it — costs the user the
 * entire page, not the panel they opened.
 *
 * It is deliberately the only boundary in this repo: everything else here is
 * first-party code where a crash is our bug and should be loud in development.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = {
  kind: string;
  /** Changes whenever a DIFFERENT attempt should be made — a different view
   * file, or the same file edited. Resetting on `kind` alone was not enough:
   * the IDE mounts ONE renderer and swaps its `path`, so two files of the same
   * kind share this instance. A crash in one then followed the user to the
   * other (reporting the wrong file's error), and repairing a file in place —
   * an agent write, or the gear panel's own save — never cleared it, leaving a
   * fixed file permanently broken for the life of the tab. */
  resetKey: string;
  children: ReactNode;
};
type State = { error: Error | null };

export class ViewErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Keep the stack reachable — the panel below only shows the message, and a
    // plug-in author debugging their own kind needs the component trace.
    console.error(`view kind "${this.props.kind}" threw while rendering`, error, info.componentStack);
  }

  componentDidUpdate(prev: Props): void {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div role="status" className="ev-banner">
        <span className="ev-banner__icon" aria-hidden>
          ⚠
        </span>
        <div className="ev-banner__body">
          This view failed to render. <code>{this.props.kind}</code>: {error.message}
        </div>
      </div>
    );
  }
}
