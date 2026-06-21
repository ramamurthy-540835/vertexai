export type QueryTemplate = {
  label: string;
  question: string;
  filterHint: string;
};

export const queryTemplates: QueryTemplate[] = [
  {
    label: "Exact & high-confidence matches",
    question: "Show exact and high-confidence matches",
    filterHint: "matchType=exact, minScore=95",
  },
  {
    label: "Needs manual review",
    question: "Which matches need manual review?",
    filterHint: "manualReview=true",
  },
  {
    label: "Fuzzy matches below threshold",
    question: "List fuzzy matches under score 95",
    filterHint: "matchType=fuzzy, maxScore=95",
  },
  {
    label: "Unmatched leads",
    question: "Which leads have no POS match?",
    filterHint: "unmatched=true",
  },
  {
    label: "Top transactions by amount",
    question: "What are the highest-value matched transactions?",
    filterHint: "sort=amount desc",
  },
  {
    label: "Match summary for this warehouse",
    question: "Give a summary of match quality for this warehouse",
    filterHint: "summary",
  },
];
