import { Btn } from "../../components/Btn";

/**
 * The reader's way back — ONE button for every sentence a reader can meet
 * that may be a moment's: an entry a restoring sandbox answered "not there"
 * for, an entry or view file that could not be read. Always a re-read, never
 * a build. One component so the wording, and whatever it later gains (an
 * `aria-describedby`, a cooldown), reaches every door at once — three copies
 * of it had already appeared by review round 6.
 */
export function TryAgain({ onClick }: { onClick: () => void }) {
  return (
    <Btn size="sm" onClick={onClick}>
      Try again
    </Btn>
  );
}
