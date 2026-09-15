"""Echo v0.1 demo: all six category models on synthetic tasks, one panel each,
plus the integration proof: Echo's WHO model wrapped by the Substrate gate."""
import math, numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from wwwwwh import (BradleyTerryWho, DirichletWhat, WeibullWhen, KDEWhere,
                    AttributionWhy, MarkovHow)
from engine import TestMartingale, longshot_correct, sigmoid, logit

rng = np.random.default_rng(42)
fig, ax = plt.subplots(2, 3, figsize=(13, 7.5))

# ---------------- WHO: 12-team season, Elo learns true strengths -------------
who = BradleyTerryWho()
teams = [f"T{i}" for i in range(12)]
true_s = {t: rng.normal(0, 1) for t in teams}
games = []
for g in range(900):
    a, b = rng.choice(teams, 2, replace=False)
    p = 1/(1+math.exp(-(true_s[a]-true_s[b])))
    y = int(rng.random() < p); games.append((a, b, y, p))
    who.update(a, b, y)
order = sorted(teams, key=lambda t: -true_s[t])
ax[0,0].bar(range(12), [who.r[t] for t in order], color="#4477aa")
ax[0,0].plot(range(12), [1500+220*true_s[t] for t in order], "k--", lw=1.2,
             label="true strength (scaled)")
ax[0,0].set_title("WHO — Elo vs truth"); ax[0,0].legend(fontsize=7)
ax[0,0].set_xticks([])

# ---------------- WHAT: outcome-type distribution learning -------------------
cats = ["landfall_FL", "landfall_TX", "landfall_other", "no_landfall"]
what = DirichletWhat(cats)
true_p = np.array([.34, .22, .14, .30])
draws = rng.choice(4, 400, p=true_p)
est_hist = []
for d in draws:
    what.update(cats[d]); est_hist.append([what.predict()[c] for c in cats])
est_hist = np.array(est_hist)
for j, c in enumerate(cats):
    ax[0,1].plot(est_hist[:,j], lw=1.4, label=c)
    ax[0,1].axhline(true_p[j], ls=":", c="gray", lw=.8)
ax[0,1].set_title("WHAT — Dirichlet convergence"); ax[0,1].legend(fontsize=6)

# ---------------- WHEN: Weibull timing fit + window probs --------------------
true_when = (1.8, 30.0)                       # shape, scale (days)
waits = true_when[1]*rng.weibull(true_when[0], 350)
when = WeibullWhen().fit(waits)
t = np.linspace(0.5, 90, 200)
ax[0,2].plot(t, [when.hazard(x) for x in t], lw=1.6, label=f"fit k={when.k:.2f}, λ={when.lam:.1f}")
tk = true_when
ax[0,2].plot(t, [(tk[0]/tk[1])*((x/tk[1])**(tk[0]-1)) for x in t], "k--", lw=1, label="true hazard")
ax[0,2].set_title("WHEN — hazard recovery"); ax[0,2].legend(fontsize=7)
w30 = when.window_prob(0, 30); w60 = when.window_prob(30, 60)

# ---------------- WHERE: KDE landfall density + region prob ------------------
centers = np.array([[0,0],[3.4,1.2],[1.5,-2.2]])
pts = np.vstack([c + rng.normal(0,.7,(120,2)) for c in centers])
where = KDEWhere().fit(pts)
gx, gy = np.meshgrid(np.linspace(-3,6,120), np.linspace(-5,4,120))
dens = where.density(np.c_[gx.ravel(), gy.ravel()]).reshape(gx.shape)
ax[1,0].contourf(gx, gy, dens, 24, cmap="magma")
circ = plt.Circle((3.4,1.2), 1.0, fill=False, ec="cyan", lw=1.5)
ax[1,0].add_patch(circ)
pr = where.region_prob((3.4,1.2), 1.0, rng=rng)
ax[1,0].set_title(f"WHERE — KDE, P(region)={pr:.2f}")

# ---------------- WHY: attribution audit -------------------------------------
n = 1200
X = rng.normal(0, 1, (n, 4))
y = (rng.random(n) < sigmoid(1.3*X[:,0] + 0.6*X[:,1] + 0.0*X[:,2] + 0.15*X[:,3])).astype(float)
why = AttributionWhy(["injury_news","rest_days","jersey_color","travel_km"]).fit(X, y)
att = why.attribution()
ax[1,1].barh(list(att.keys())[::-1], list(att.values())[::-1], color="#cc6677")
ax[1,1].set_title("WHY — LOO info attribution (bits-ish)")

# ---------------- HOW: pathway model ------------------------------------------
how = MarkovHow()
paths = [["form_dip","injury","line_move","upset"],
         ["form_dip","line_move","hold"],
         ["injury","line_move","upset"],
         ["form_dip","injury","line_move","upset"],
         ["stable","hold"]]*40
for p in paths: how.update(p)
states = sorted(how.vocab)
M = np.array([[how.next_dist(a).get(b,0) for b in states] for a in states])
im = ax[1,2].imshow(M, cmap="viridis")
ax[1,2].set_xticks(range(len(states))); ax[1,2].set_xticklabels(states, rotation=45, fontsize=6)
ax[1,2].set_yticks(range(len(states))); ax[1,2].set_yticklabels(states, fontsize=6)
p_upset = how.path_prob(["form_dip","injury","line_move","upset"])
ax[1,2].set_title(f"HOW — transitions, P(upset path)={p_upset:.2f}")

fig.suptitle("ECHO ENGINE v0.1 — working WWWWWH category models", y=.995)
fig.tight_layout(); fig.savefig("echo_models.png", dpi=140)

# ---------------- INTEGRATION: Echo WHO expert through the Substrate gate ----
# Second synthetic half-season: market = biased truth; Echo-Elo = structural expert.
mart_echo = TestMartingale(threshold=20)
briers = {"market": [], "echo_who": []}
for g in range(700):
    a, b = rng.choice(teams, 2, replace=False)
    p = 1/(1+math.exp(-(true_s[a]-true_s[b])))
    m = float(np.clip(sigmoid(1.3*logit(p) + rng.normal(0,.2)), .02, .98))
    m_null = longshot_correct(m, 0.0, 0.85)
    q = float(np.clip(who.predict(a, b), .02, .98))
    y = int(rng.random() < p)
    who.update(a, b, y)
    mart_echo.update(0.7*q + 0.3*m_null, m_null, y)     # shrunk Echo vs market null
    briers["market"].append((m_null-y)**2); briers["echo_who"].append((q-y)**2)

print("WHEN window probs: P(0-30d)=%.3f  P(30-60d|>30)=%.3f" % (w30, w60))
print("WHERE region prob: %.3f" % pr)
print("WHY attribution:", {k: round(v,4) for k,v in att.items()})
print("HOW upset-path prob: %.3f" % p_upset)
print("INTEGRATION  Brier market=%.4f  echo_who=%.4f  E_final=%.1f  certified=%s"
      % (np.mean(briers["market"]), np.mean(briers["echo_who"]),
         mart_echo.E, mart_echo.certified))
