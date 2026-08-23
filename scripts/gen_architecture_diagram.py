"""Generates architecture.excalidraw: two diagrams on one canvas.

Top: the AWS deployment, grouped by trust boundary.
Bottom: the original high-level design, transcribed from
"high level design.jpg".

Laid out on a strict column grid.

Columns A/B/C/D, with the gaps between them reserved as arrow corridors:
    340-380 (x=352/360)    650-690 (x=655/668/682)    1170-1210 (x=1190)
Nothing is ever placed in a corridor, so cross-lane arrows have clear paths.
"""
import json
import random

random.seed(11)
els = []


def _seed():
    return random.randint(1, 2**31)


def _base(eid, typ, x, y, w, h, stroke="#1e1e1e", bg="transparent",
          stroke_style="solid", stroke_width=2, roundness=None):
    return {
        "id": eid, "type": typ, "x": x, "y": y, "width": w, "height": h,
        "angle": 0, "strokeColor": stroke, "backgroundColor": bg,
        "fillStyle": "solid", "strokeWidth": stroke_width,
        "strokeStyle": stroke_style, "roughness": 0, "opacity": 100,
        "groupIds": [], "frameId": None, "roundness": roundness,
        "seed": _seed(), "version": 1, "versionNonce": _seed(),
        "isDeleted": False, "boundElements": [], "updated": 1,
        "link": None, "locked": False,
    }


def rect(eid, x, y, w, h, stroke="#1e1e1e", bg="transparent",
         stroke_style="solid", stroke_width=2):
    els.append(_base(eid, "rectangle", x, y, w, h, stroke, bg, stroke_style,
                     stroke_width, roundness={"type": 3}))


def ellipse(eid, x, y, w, h, stroke="#1e1e1e", bg="transparent"):
    els.append(_base(eid, "ellipse", x, y, w, h, stroke, bg))


def text(eid, x, y, w, h, content, size=16, color="#1e1e1e", align="left"):
    e = _base(eid, "text", x, y, w, h, stroke=color)
    e.update({
        "text": content, "fontSize": size, "fontFamily": 2,
        "textAlign": align, "verticalAlign": "top", "containerId": None,
        "originalText": content, "autoResize": False, "lineHeight": 1.25,
    })
    els.append(e)


def arrow(eid, pts, color="#1e1e1e", stroke_style="solid", both=False):
    x0, y0 = pts[0]
    rel = [[px - x0, py - y0] for px, py in pts]
    xs = [p[0] for p in rel]
    ys = [p[1] for p in rel]
    e = _base(eid, "arrow", x0, y0, max(xs) - min(xs), max(ys) - min(ys),
              stroke=color, stroke_style=stroke_style)
    e.update({
        "points": rel, "lastCommittedPoint": None, "startBinding": None,
        "endBinding": None, "startArrowhead": "arrow" if both else None,
        "endArrowhead": "arrow", "elbowed": False,
    })
    els.append(e)


GRAY, BLUE, GREEN, RED, YELLOW, PURPLE = (
    "#868e96", "#1971c2", "#2f9e44", "#e03131", "#f08c00", "#9c36b5")
BG_BLUE, BG_GREEN, BG_RED, BG_YELLOW, BG_PURPLE = (
    "#a5d8ff", "#b2f2bb", "#ffc9c9", "#ffec99", "#eebefa")

A, B, C, D = 70, 380, 690, 1210
AW = BW = 270
CW, DW = 480, 450
LBL = "#495057"


def lane(eid, y, h, label, bg):
    rect(eid, 40, y, 1640, h, stroke=GRAY, bg=bg, stroke_width=1)
    text(eid + "_t", 56, y + 10, 700, 22, label, size=15)


def box(eid, x, y, w, h, title, body="", stroke="#1e1e1e", bg="transparent",
        dashed=False, tsize=14):
    rect(eid, x, y, w, h, stroke=stroke, bg=bg,
         stroke_style="dashed" if dashed else "solid")
    text(eid + "_t", x + 12, y + 10, w - 24, 20, title, size=tsize)
    if body:
        text(eid + "_b", x + 12, y + 14 + tsize + 6, w - 24, h - 44, body,
             size=12, color=LBL)


text("t1", 40, 20, 900, 34, "Ledger - AWS Architecture", size=28)
text("t2", 40, 58, 1100, 22,
     "Grouped by trust boundary: who is able to change what.",
     size=14, color=GRAY)

# ================= LANE 1 =================
lane("l1", 96, 210, "1  .  YOUR LAPTOP  -  full admin (AWS_PROFILE=ledger)",
     "#f8f9fa")
box("tfboot", A, 130, AW, 70, "terraform/bootstrap",
    "S3 state bucket  .  run once", stroke=PURPLE, bg=BG_PURPLE)
box("tfmain", A, 212, AW, 70, "terraform/",
    "the 10 app resources", stroke=PURPLE, bg=BG_PURPLE)
box("deploysh", B, 130, BW, 70, "scripts/deploy.sh",
    "manual deploy: fresh stack,\nor code not on main", stroke=GRAY)
box("provsh", B, 212, BW, 70, "scripts/provision_client.sh",
    "mints an API key", stroke=GRAY)
box("n1", C, 130, CW, 152, "Infrastructure changes are manual",
    "terraform apply runs here, with your credentials -\nnever in CI.\n\n"
    "The CD role can deploy the application but cannot\n"
    "modify infrastructure, so a compromised workflow\n"
    "cannot rewrite the account.",
    stroke=YELLOW, bg=BG_YELLOW)
box("n2", D, 130, DW, 152, "Cost  -  destroy between sessions",
    "~$22/month if left running:\n$15 database + $7 per nano node.\n\n"
    "Lightsail bills hourly and prorates on deletion.\n"
    "Disabling a service does NOT stop charges -\nonly deletion does.",
    stroke=RED, bg=BG_RED)

# ================= LANE 2 =================
lane("l2", 330, 250, "2  .  GITHUB  -  dzldebby/ledger", "#f1f3f5")
box("repo", A, 400, AW, 70, "Repository", "main branch")
box("ci", B, 385, BW, 100, "CI  -  ci.yml",
    "pytest against a postgres:16\nservice container\n\nruns on push and PR",
    stroke=GREEN, bg=BG_GREEN)
rect("cd", C, 372, CW, 126, stroke=GREEN, bg=BG_GREEN)
text("cd_t", C + 14, 380, 300, 20, "CD  -  cd.yml", size=14)
box("cd1", C + 14, 406, CW - 28, 26, "1.  assume ledger-cd via OIDC",
    stroke=GREEN, bg="#ffffff", tsize=12)
box("cd2", C + 14, 438, CW - 28, 26,
    "2.  deploy.sh  -  build, push, create deployment",
    stroke=GREEN, bg="#ffffff", tsize=12)
box("cd3", C + 14, 470, CW - 28, 26,
    "3.  smoke test /health  -  fail the job on non-200",
    stroke=GREEN, bg="#ffffff", tsize=12)
box("n3", D, 372, DW, 126, "Triggered by workflow_run, not push",
    "A red test suite can never deploy. Checks out\n"
    "workflow_run.head_sha - such jobs otherwise run\n"
    "against the tip of main, not the commit CI validated.\n"
    "Auth is a short-lived OIDC token: no AWS keys in\nGitHub secrets.",
    stroke=YELLOW, bg=BG_YELLOW)

# ================= LANE 3 =================
lane("l3", 610, 620, "3  .  AWS ACCOUNT 302127759466  -  us-east-1", "#ffffff")
box("s3", A, 665, AW, 110, "S3  -  Terraform state",
    "ledger-terraform-state-...\nversioned . encrypted . locking\n\n"
    "Separate stack: survives destroy,\nsince state outlives its resources.",
    stroke=PURPLE, bg=BG_PURPLE)
box("oidc", A, 790, AW, 78, "IAM  -  OIDC provider",
    "token.actions.githubusercontent.com\naud: sts.amazonaws.com",
    stroke=YELLOW, bg=BG_YELLOW)
box("cdrole", A, 883, AW, 200, "IAM role  -  ledger-cd",
    "trusts sub:\nrepo:dzldebby@19401055/\n  ledger@1330920517:ref:refs/heads/main\n\n"
    "MAY: push this ECR repo . deploy this\nservice . read Lightsail status (*) .\n"
    "read one SSM parameter\n\nMAY NOT: change any infrastructure",
    stroke=YELLOW, bg=BG_YELLOW)
box("ecr", B, 665, BW, 110, "ECR  -  ledger",
    "IMMUTABLE tags\nscan on push . keep last 10\nforce_delete = true",
    stroke=RED, bg=BG_RED)
box("ssm", B, 790, BW, 110, "SSM Parameter Store",
    "/ledger/database_url\nSecureString\n\nLets CD read the connection\n"
    "string without Terraform state.", stroke=YELLOW, bg=BG_YELLOW)
rect("csbox", C, 665, CW, 235, stroke=GREEN, bg=BG_GREEN)
text("cs_t", C + 14, 675, 460, 20,
     "Lightsail Container Service  -  ledger     nano, scale=1", size=14)
box("appc", C + 14, 703, CW - 28, 92, "app container  -  uvicorn :8000",
    "entrypoint runs migrations BEFORE uvicorn starts,\n"
    "under a Postgres advisory lock - concurrent cold\n"
    "starts are safe: one migrates, the rest no-op.",
    stroke=GREEN, bg="#ffffff", tsize=13)
box("sidec", C + 14, 803, CW - 28, 88, "provisioner sidecar  -  temporary",
    "DB is private and Lightsail has no exec primitive, so\n"
    "an API-key INSERT rides in on a second container. The\n"
    "key is generated on the laptop; only its hash is sent.",
    stroke=GRAY, bg="#f8f9fa", dashed=True, tsize=13)
box("db", D, 665, 270, 235, "Lightsail DB  -  ledger-db",
    "postgres_16  .  micro_2_0\n\npublicly_accessible = false\n\n"
    "Reachable only from Lightsail\nresources in this region -\n"
    "never from a laptop.\n\nskip_final_snapshot = true, so\n"
    "destroy discards all data.", stroke=BLUE, bg=BG_BLUE)
box("n5", B, 1000, 790, 180, "Two silent-failure traps",
    "1.  The ECR pull uses ecr_image_puller_role, NOT the service's\n"
    "principal_arn. They are different ARNs. Granting the wrong one fails\n"
    "with NO error anywhere: no container, no logs, the deployment just\n"
    "reports Canceled.\n\n"
    "2.  Lightsail READ actions cannot be resource-scoped - they need\n"
    "Resource: *. Create-deployment DOES accept an ARN, which is what\n"
    "makes it confusing: the deploy succeeds, only polling is denied.",
    stroke=RED, bg=BG_RED)
box("n6", D, 1000, DW, 180, "Limitation  -  per-instance rate limiting",
    "app/auth.py holds counters in process memory. At\n"
    "scale=1 this is exact. At scale=2 each replica counts\n"
    "independently, so a client's effective limit becomes\n"
    "up to 2x the configured value.\n\n"
    "Accepted, not a bug here - a correct limiter needs\n"
    "shared state such as Redis.\n\n"
    "The HLD calls for scale=2; this runs 1 to halve cost.",
    stroke=YELLOW, bg=BG_YELLOW)

# ================= LANE 4 =================
lane("l4", 1250, 110, "4  .  PUBLIC INTERNET", "#f8f9fa")
ellipse("enduser", 700, 1278, 190, 62, bg="#ffffff")
text("enduser_t", 715, 1292, 160, 40, "End user\n(browser)", size=14,
     align="center")
text("euser_note", 1230, 1275, 430, 70,
     "X-API-Key on every request; SHA-256 hashed at rest.\n"
     "Writes need a unique Idempotency-Key - replaying with an\n"
     "identical body returns the original transaction; a different\n"
     "body returns 409.", size=12, color=LBL)

# ================= ARROWS =================
arrow("a_tf", [(A, 247), (18, 247), (18, 990), (40, 990)],
      color=PURPLE, stroke_style="dashed")
text("a_tf_lbl", 70, 287, 420, 16,
     "terraform apply - creates everything in lane 3", size=12, color=LBL)

arrow("a_push", [(205, 306), (205, 400)])
text("a_push_lbl", 216, 374, 160, 16, "git push main", size=12, color=LBL)
arrow("a_ci", [(340, 435), (B, 435)])
arrow("a_cd", [(650, 435), (C, 435)])
text("a_cd_lbl", 596, 512, 240, 16, "workflow_run - on CI success",
     size=12, color=LBL)

arrow("a_assume", [(C, 419), (654, 419), (654, 632), (360, 632), (360, 950),
                   (340, 950)], color=YELLOW)
text("a_assume_lbl", 430, 588, 280, 16, "AssumeRoleWithWebIdentity",
     size=12, color=LBL)
arrow("a_trust", [(160, 883), (160, 868)], color=YELLOW, stroke_style="dashed")
text("a_trust_lbl", 172, 862, 200, 16, "validates the token", size=11,
     color=LBL)

arrow("a_pushimg", [(C, 451), (662, 451), (662, 645), (505, 645), (505, 665)],
      color=RED)
text("a_pushimg_lbl", 170, 643, 250, 16, "docker push (tag = SHA)",
     size=12, color=LBL, align="right")
arrow("a_ssm", [(C, 483), (670, 483), (670, 655), (352, 655), (352, 845),
                (B, 845)])
arrow("a_deploy", [(930, 498), (930, 665)])
text("a_deploy_lbl", 944, 566, 300, 18, "CreateContainerServiceDeployment",
     size=12, color=LBL)
arrow("a_smoke", [(1080, 498), (1080, 665)], stroke_style="dashed")
text("a_smoke_lbl", 1094, 620, 110, 18, "GET /health", size=12, color=LBL)

arrow("a_pull", [(C, 720), (650, 720)], color=RED)
arrow("a_sql", [(1156, 740), (D, 740)], color=BLUE)
text("a_sql_lbl", 1104, 646, 180, 14, "SQL over the private network",
     size=11, color=LBL)
arrow("a_sqlside", [(1156, 847), (1184, 847), (1184, 812), (D, 812)],
      color=BLUE, stroke_style="dashed")
text("a_sqlside_lbl", 1046, 912, 180, 16, "INSERT api_clients",
     size=11, color=LBL)

arrow("a_prov", [(650, 247), (678, 247), (678, 847), (C + 14, 847)],
      color=GRAY, stroke_style="dashed")
text("a_prov_lbl", 392, 296, 252, 32, "adds, then removes,\nthe sidecar",
     size=12, color=LBL, align="right")
arrow("a_manual", [(650, 165), (684, 165), (684, 640), (860, 640),
                   (860, 665)], color=GRAY, stroke_style="dashed")
text("a_manual_lbl", 700, 614, 240, 18, "same script, run by hand", size=12,
     color=LBL)

arrow("a_user", [(795, 1278), (1190, 1278), (1190, 890), (1170, 890)])
text("a_user_lbl", 902, 1216, 286, 18, "HTTPS  -  /ui, /accounts, /transactions",
     size=12, color=LBL)

# =====================================================================
# SECOND DIAGRAM: the original high-level design, below the AWS one.
# Transcribed from "high level design.jpg" so both live in one canvas.
# =====================================================================
rect("divider", 40, 1440, 1640, 2, stroke=GRAY, stroke_width=1)

text("h_t1", 40, 1480, 900, 34, "High-Level Design", size=28)
text("h_t2", 40, 1518, 1200, 22,
     "The target design. This deployment implements the synchronous write "
     "path (1); risk, compliance and settlement are out of scope.",
     size=14, color=GRAY)

# ---- 1) main synchronous write path ----
text("h_s1", 80, 1690, 420, 20, "1)  Main synchronous write path", size=15,
     color=BLUE)
text("h_s1b", 80, 1714, 420, 16,
     "*traceparent carried across all HTTP hops", size=11, color=LBL)

box("h_riskdb", 620, 1570, 200, 62, "Risk DB",
    "1. Risk rules\n2. Risk decision records", stroke=BLUE, bg=BG_BLUE,
    tsize=13)
box("h_riskapp", 600, 1660, 240, 100, "Risk App",
    "Approve, Decline, Review\ntransactions based on\nrisk rules",
    stroke=BLUE, bg=BG_BLUE, tsize=13)

box("h_client", 60, 1870, 130, 55, "Client", stroke="#1e1e1e", tsize=13)
text("h_client_l", 60, 1930, 140, 16, "Postman / SPA", size=11, color=LBL)
box("h_apigw", 240, 1870, 140, 55, "API Gateway", stroke="#1e1e1e", tsize=13)
text("h_apigw_l", 240, 1930, 150, 16, "Auth, rate limiting", size=11,
     color=LBL)
box("h_lb", 430, 1870, 140, 55, "Load Balancer", stroke="#1e1e1e", tsize=13)
text("h_lb_l", 430, 1930, 160, 44,
     "Distribute traffic across\nmultiple ledger app\ninstances", size=11,
     color=LBL)

box("h_ledgerapp", 620, 1840, 250, 170, "Ledger App",
    "2 instances, stateless\n\n"
    "1. Validate idempotency\n"
    "2. Apply pessimistic concurrency\n     (row-locking)\n"
    "3. Call Risk Service; waits\n     permission before posting",
    stroke=BLUE, bg=BG_BLUE, tsize=13)
box("h_ledgerdb", 910, 1840, 260, 230, "Ledger DB",
    "Tables:\n1. accounts\n2. transactions + reversal_of_id\n3. postings\n"
    "4. balances\n5. outbox events + traceparent\n6. idempotency_records\n"
    "7. settlements\n8. holds\n\n"
    "Ledger changes + idempotency results\n+ outbox event commit in one\n"
    "ACID transaction", stroke=BLUE, bg=BG_BLUE, tsize=13)

# ---- 2) asynchronous compliance flow ----
text("h_s2", 1210, 1690, 460, 20, "2)  Asynchronous compliance flow",
     size=15, color=GREEN)
text("h_s2b", 1210, 1714, 460, 16,
     "async + traceparent in message headers", size=11, color=LBL)
rect("h_compbox", 1190, 1742, 830, 330, stroke=GRAY, stroke_style="dashed",
     stroke_width=1)

box("h_dlq", 1620, 1772, 120, 38, "DLQ", stroke=GREEN, bg=BG_GREEN, tsize=12)
text("h_dlq_l", 1752, 1784, 140, 16, "after N retries", size=11, color=LBL)

box("h_outbox", 1220, 1840, 160, 66, "Outbox Publisher", stroke=GREEN,
    bg=BG_GREEN, tsize=12)
text("h_outbox_l", 1220, 1912, 170, 50,
     "Polls outbox_events on a\nschedule and publishes\nto message broker",
     size=10, color=LBL)
box("h_broker", 1420, 1840, 160, 66, "Message Broker", stroke=GREEN,
    bg=BG_GREEN, tsize=12)
text("h_broker_l", 1420, 1912, 170, 50,
     "1. at-least once delivery\n2. provides buffer during\ntraffic spikes",
     size=10, color=LBL)
box("h_compapp", 1620, 1840, 160, 66, "Compliance App", stroke=GREEN,
    bg=BG_GREEN, tsize=12)
text("h_compapp_l", 1620, 1912, 175, 76,
     "1. Checks rule violation\n2. Writes alert to\ncompliance DB\n"
     "Note: async; ledger does\nnot wait for compliance", size=10, color=LBL)
box("h_compdb", 1830, 1840, 170, 66, "Compliance Database", stroke=GREEN,
    bg=BG_GREEN, tsize=12)
text("h_compdb_l", 1830, 1912, 175, 34, "Stores alerts and\nreview status",
     size=10, color=LBL)

# ---- 3) settlement flow ----
text("h_s3", 80, 2120, 420, 20, "3)  Settlement flow for business accts",
     size=15, color=RED)
text("h_s3b", 80, 2144, 420, 34,
     "Settlement job starts its own trace\nand propagates it to Ledger App",
     size=11, color=LBL)

box("h_sched", 300, 2215, 160, 66, "Settlement Scheduler", stroke=RED,
    bg=BG_RED, tsize=12)
box("h_settleapp", 620, 2110, 250, 200, "Settlement App (saga orchestrator)",
    "1. Finds merchants due for\n     settlement\n"
    "2. Asks Ledger App to reserve\n     funds\n"
    "3. Calls external bank\n"
    "4. Tracks settlement.state on\n     ledgerDB settlement tables\n"
    "     (Confirmed, Failed, Unknown)", stroke=RED, bg=BG_RED, tsize=13)
box("h_bank", 940, 2180, 170, 66, "External Bank", stroke="#1e1e1e",
    tsize=13)

# ---- 4) observability ----
rect("h_obsbox", 60, 2400, 1240, 350, stroke=PURPLE, stroke_style="dashed",
     stroke_width=1)
text("h_s4", 84, 2418, 300, 20, "Observability", size=15, color=PURPLE)
text("h_s4b", 84, 2444, 1180, 16,
     "Tracing spans across (1) API Gateway (2) Load Balancer (3) Ledger App "
     "(4) Risk App (5) Outbox Publisher (6) Message Broker "
     "(7) Compliance App (8) Settlement Service", size=11, color=LBL)

box("h_otel", 540, 2490, 240, 48, "OpenTelemetry Collector", stroke=PURPLE,
    bg=BG_PURPLE, tsize=13)
box("h_trace", 420, 2585, 140, 44, "Trace", stroke=PURPLE, bg=BG_PURPLE,
    tsize=13)
box("h_metrics", 590, 2585, 140, 44, "Metrics", stroke=PURPLE, bg=BG_PURPLE,
    tsize=13)
box("h_logs", 760, 2585, 140, 44, "Logs", stroke=PURPLE, bg=BG_PURPLE,
    tsize=13)
box("h_dash", 560, 2680, 200, 48, "Monitoring Dashboard", stroke=PURPLE,
    bg=BG_PURPLE, tsize=13)

# ---- HLD arrows ----
arrow("h_a1", [(190, 1897), (240, 1897)])
arrow("h_a2", [(380, 1897), (430, 1897)])
arrow("h_a3", [(570, 1897), (620, 1897)])
arrow("h_a4", [(870, 1900), (910, 1900)], both=True)
# response path back to the client
arrow("h_a5", [(620, 1985), (592, 1985), (592, 2060), (125, 2060), (125, 1925)])
arrow("h_a6", [(745, 1840), (745, 1760)], both=True)
arrow("h_a7", [(720, 1660), (720, 1632)], both=True)

arrow("h_a8", [(1170, 1873), (1220, 1873)], color=GREEN)
arrow("h_a9", [(1380, 1873), (1420, 1873)], color=GREEN)
arrow("h_a10", [(1580, 1873), (1620, 1873)], color=GREEN)
arrow("h_a11", [(1780, 1873), (1830, 1873)], color=GREEN)
arrow("h_a12", [(1700, 1840), (1700, 1810)], color=GREEN)

arrow("h_a13", [(460, 2248), (620, 2248)], color=RED)
arrow("h_a14", [(745, 2110), (745, 2010)], color=RED, both=True)
arrow("h_a15", [(870, 2213), (940, 2213)], color=RED, both=True)

arrow("h_a16", [(620, 2538), (490, 2585)], color=PURPLE)
arrow("h_a17", [(660, 2538), (660, 2585)], color=PURPLE)
arrow("h_a18", [(700, 2538), (830, 2585)], color=PURPLE)
arrow("h_a19", [(490, 2629), (620, 2680)], color=PURPLE)
arrow("h_a20", [(660, 2629), (660, 2680)], color=PURPLE)
arrow("h_a21", [(830, 2629), (700, 2680)], color=PURPLE)

doc = {"type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
       "elements": els,
       "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
       "files": {}}
out = r"C:/Users/Debby/Documents/ledger/architecture.excalidraw"
with open(out, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2)
print("wrote", out, "|", len(els), "elements")
