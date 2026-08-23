/**
 * Which questions each page answers, in one list (T-303).
 *
 * The same shape, and the same reason, as src/lib/nav.ts: a question about
 * billing must disappear from the page *and* from its `FAQPage` JSON-LD when
 * the billing module is off, and the only way to guarantee that is for both to
 * read one list. Answering "do I need a card to start?" on a product with no
 * checkout is the marketing half of the half-applied toggle
 * docs/runbooks/module-toggle-guide.md warns about -- and doing it in
 * structured data is worse, because it is the half nobody looks at.
 *
 * Answers are deliberately plain sentences with no links in them.
 * `acceptedAnswer.text` is prose a search result may quote verbatim, and the
 * markup that renders it is the same string -- keeping both to text is what
 * makes "the JSON-LD matches what a visitor reads" a property of the code
 * rather than a review item.
 */
import { modules } from "./product";

/** A question, as the two i18n keys that carry it. */
export interface FaqKeys {
  questionKey: string;
  answerKey: string;
}

function keys(group: string, name: string): FaqKeys {
  return {
    questionKey: `marketing.faq.${group}.${name}.question`,
    answerKey: `marketing.faq.${group}.${name}.answer`,
  };
}

/** The landing page's questions: what this is, and what it is made of. */
export function productFaq(): FaqKeys[] {
  return [
    keys("product", "starting_point"),
    keys("product", "modules"),
    keys("product", "stack"),
    // There is no free plan, no checkout and no card to not need when
    // billing is off.
    ...(modules.billing ? [keys("product", "no_card")] : []),
  ];
}

/**
 * The pricing page's questions.
 *
 * Every one of them is about billing, so the list has no module guard: the
 * page it belongs to is not built at all when `billing` is off (see
 * src/pages/[...locale]/pricing.astro).
 */
export function pricingFaq(): FaqKeys[] {
  return [
    keys("pricing", "change_plan"),
    keys("pricing", "trial"),
    keys("pricing", "payment"),
    keys("pricing", "limits"),
  ];
}
