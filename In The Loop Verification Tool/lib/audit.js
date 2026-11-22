/** One-sided accuracy tests for a frozen spreadsheet sampled without replacement. */

function upperTail(population, totalsCorrect) {
  const logFactorial = new Float64Array(population + 1);
  for (let i = 2; i <= population; i++) logFactorial[i] = logFactorial[i - 1] + Math.log(i);
  const choose = (n, k) => logFactorial[n] - logFactorial[k] - logFactorial[n - k];
  const totalsIncorrect = population - totalsCorrect;

  // Sum away from the mode toward the smaller tail. The ratio recurrence avoids
  // underflow from initializing at an extreme support point and walking inward.
  return (sampleSize, correctAtLeast) => {
    const first = Math.max(0, sampleSize - totalsIncorrect);
    const last = Math.min(sampleSize, totalsCorrect);
    if (correctAtLeast <= first) return 1;
    if (correctAtLeast > last) return 0;
    const mode = Math.floor((sampleSize + 1) * (totalsCorrect + 1) / (population + 2));
    const complement = correctAtLeast <= mode;
    let correct = complement ? correctAtLeast - 1 : correctAtLeast;
    let term = Math.exp(choose(totalsCorrect, correct) + choose(totalsIncorrect, sampleSize - correct)
      - choose(population, sampleSize));
    let sum = term;
    if (complement) {
      for (; correct > first && term > sum * Number.EPSILON; correct--) {
        term *= correct * (totalsIncorrect - sampleSize + correct)
          / ((totalsCorrect - correct + 1) * (sampleSize - correct + 1));
        sum += term;
      }
    } else {
      for (; correct < last && term > sum * Number.EPSILON; correct++) {
        term *= (totalsCorrect - correct) * (sampleSize - correct)
          / ((correct + 1) * (totalsIncorrect - sampleSize + correct + 1));
        sum += term;
      }
    }
    return Math.min(1, Math.max(0, complement ? 1 - sum : sum));
  };
}

function atMost(probability, limit) {
  // The factorial log differences accumulate rounding error on large sheets.
  return probability <= limit * (1 + 3e-10);
}

function stableCount(rate, population) {
  const product = rate * population;
  const whole = Math.round(product);
  return Math.abs(product - whole) <= 4 * Number.EPSILON * product ? whole : product;
}

/** Choose the smallest fixed sample that meets both the risk and power constraints. */
export function planAudit(population, { alpha, floor, designAccuracy, power }) {
  if (!Number.isSafeInteger(population) || population < 1 || population > 25_000) {
    throw new Error('Use a spreadsheet of 1 to 25,000 rows for the accuracy test.');
  }
  if (![alpha, floor, designAccuracy, power].every(value => Number.isFinite(value) && value >= 0 && value <= 1)
      || alpha <= 0 || alpha >= power || floor >= designAccuracy) {
    throw new Error('Choose an alpha above 0, power higher than alpha, and design accuracy above the accuracy floor.');
  }
  // Stabilize conversions at exact integer boundaries after decimal input.
  const nullCorrect = Math.floor(stableCount(floor, population));
  const designCorrect = Math.ceil(stableCount(designAccuracy, population));
  if (designCorrect <= nullCorrect) throw new Error('Raise the design accuracy for this population size.');
  const nullTail = upperTail(population, nullCorrect);
  const alternativeTail = upperTail(population, designCorrect);
  let criticalCorrect = 1;
  for (let sampleSize = 1; sampleSize <= population; sampleSize++) {
    let typeIError = nullTail(sampleSize, criticalCorrect);
    // Adding one draw can move the smallest valid rejection threshold by at most one.
    if (!atMost(typeIError, alpha)) typeIError = nullTail(sampleSize, ++criticalCorrect);
    const achievedPower = alternativeTail(sampleSize, criticalCorrect);
    const meetsPower = power === 1 ? criticalCorrect <= sampleSize - (population - designCorrect)
      : atMost(power, achievedPower);
    if (criticalCorrect <= sampleSize && meetsPower) {
      return { population, alpha, floor, designAccuracy, power, nullCorrect, designCorrect,
        effectiveNullAccuracy: nullCorrect / population, effectiveDesignAccuracy: designCorrect / population,
        sampleSize, criticalCorrect, typeIError, achievedPower };
    }
  }
  throw new Error('Could not find a sample that meets the accuracy test settings.');
}

/** Make a uniform sample without replacement using rejection-sampled browser randomness. */
export function drawAuditSample(ids, size) {
  if (!Number.isSafeInteger(size) || size < 1 || size > ids.length || new Set(ids).size !== ids.length) {
    throw new Error('The audit needs distinct row IDs and a valid sample size.');
  }
  const rows = [...ids];
  const word = new Uint32Array(1);
  const range = 2 ** 32;
  for (let i = 0; i < size; i++) {
    const width = rows.length - i;
    const limit = range - range % width;
    do globalThis.crypto.getRandomValues(word); while (word[0] >= limit);
    const other = i + word[0] % width;
    [rows[i], rows[other]] = [rows[other], rows[i]];
  }
  return rows.slice(0, size);
}

/** Report possible test results when some sampled claims could not be adjudicated. */
export function evaluateAudit(plan, { correct, incorrect, inconclusive, remaining }) {
  const counts = { correct, incorrect, inconclusive, remaining };
  const values = Object.values(counts);
  if (!values.every(value => Number.isSafeInteger(value) && value >= 0)
      || values.reduce((sum, value) => sum + value, 0) !== plan.sampleSize) {
    throw new Error('Review counts must account for every row in the fixed sample.');
  }
  if (remaining) return { status: 'pending', ...counts, pValueMin: null, pValueMax: null };
  const tail = upperTail(plan.population, plan.nullCorrect);
  const pValueMin = tail(plan.sampleSize, correct + inconclusive);
  const pValueMax = tail(plan.sampleSize, correct);
  const status = atMost(pValueMax, plan.alpha) ? 'pass'
    : atMost(pValueMin, plan.alpha) ? 'inconclusive' : 'not-demonstrated';
  return { status, ...counts, pValueMin, pValueMax };
}
