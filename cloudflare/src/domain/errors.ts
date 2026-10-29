export interface FieldIssue {
  path: (string | number)[];
  message: string;
}
export class PlayError extends Error {
  constructor(
    message: string,
    public issues: FieldIssue[] = [],
  ) {
    super(message);
  }
}
