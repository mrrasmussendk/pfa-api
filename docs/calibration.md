# Calibrating the token estimator

`tools/brokkr/tokenization.py` is the single source of truth for token counting — imported
by both Eitri (the EIT100/EIT101 check gate) and the `heimdall` CLI (`heimdall estimate`),
so the harness and the gate cannot drift.

To re-check its calibration against a real tokenizer, compare `heimdall estimate` with
tiktoken's `o200k_base` on any Python tree:

    heimdall estimate src            # estimator total for all .py under src/

    pip install tiktoken
    python -c "import sys,os,tiktoken; enc=tiktoken.get_encoding('o200k_base'); \
      print(sum(len(enc.encode(open(os.path.join(r,f),encoding='utf-8').read())) \
      for r,_,fs in os.walk(sys.argv[1]) for f in fs if f.endswith('.py')))" src

Target: within ~±10% of `o200k_base`, erring conservative (+) on typical Python — a stricter
budget is the safe failure mode. Run the comparison on your own tree before trusting the
number to the token. If you retune the estimator, `tests/tooling/test_token_estimator.py`
pins the regex to the reference loop — update both, and the claim in [Development tooling](tooling.md#how-the-token-budget-works).
