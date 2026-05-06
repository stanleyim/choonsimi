# date 이후 추가

df["price"] = pd.to_numeric(df.get("close", None), errors="coerce")

if "final_score" in df.columns:
    df["final_score"] = df["final_score"]
