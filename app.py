import asyncio
from datetime import datetime
import itertools
import re
import streamlit as st
from playwright.async_api import async_playwright

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Multi-Bet Parlay Scraper", layout="wide")

# Custom CSS matching dark theme style
st.markdown(
    """
<style>
.stApp {
    background-color: #121212;
    color: #f0f0f0;
}
.parlay-box {
    background-color: #1e1e1e;
    color: #f0f0f0;
    padding: 15px;
    border-radius: 8px;
    margin-bottom: 15px;
    font-family: monospace;
    border-left: 5px solid #4CAF50;
}
.parlay-box-out {
    background-color: #2e1e1e;
    color: #f0f0f0;
    padding: 15px;
    border-radius: 8px;
    margin-bottom: 15px;
    font-family: monospace;
    border-left: 5px solid #ff9800;
}
.parlay-header {
    font-size: 16px;
    font-weight: bold;
    color: #4CAF50;
    margin-bottom: 8px;
}
.parlay-header-out {
    font-size: 16px;
    font-weight: bold;
    color: #ff9800;
    margin-bottom: 8px;
}
.parlay-leg {
    margin-left: 15px;
    margin-bottom: 4px;
    color: #d4d4d4;
}
</style>
""",
    unsafe_allow_html=True,
)

# --- MAPPINGS ---
book_mapping = {
    "bet365": "21",
    "betmgm": "4",
    "mgm": "4",
    "caesars": "3",
    "czr": "3",
    "draftkings": "2",
    "dk": "2",
    "fanatics": "22",
    "fanduel": "1",
    "fd": "1",
    "hard rock": "33",
    "hard rock (oh)": "33",
    "thescore bet": "5",
    "thescore": "5",
    "the score": "5",
}

league_mapping = {
    "ALL": "0",
    "FIFA": "9",
    "LIGA MX": "12",
    "MLB": "1",
    "MLS": "10",
    "NBA": "5",
    "NCAAB": "6",
    "NCAAF": "3",
    "NCAAW": "8",
    "NFL": "2",
    "NHL": "4",
    "SERIE A": "11",
    "WNBA": "7",
}

unique_books = [
    ("DraftKings", "2"),
    ("FanDuel", "1"),
    ("Bet365", "21"),
    ("Hard Rock (OH)", "33"),
    ("BetMGM", "4"),
    ("Caesars", "3"),
    ("Fanatics", "22"),
    ("TheScore Bet", "5"),
]
unique_books_dict = dict(unique_books)


# --- MATH & HELPER FUNCTIONS ---
def american_to_decimal(american_odds):
  if american_odds > 0:
    return (american_odds / 100.0) + 1.0
  else:
    return (100.0 / abs(american_odds)) + 1.0


def decimal_to_american(decimal_odds):
  if decimal_odds >= 2.0:
    return round((decimal_odds - 1.0) * 100)
  else:
    return round(-100.0 / (decimal_odds - 1.0))


def is_today_game(raw_text):
  lower = raw_text.lower()
  if "tomorrow" in lower:
    return False

  future_date_patterns = [
      "7/29",
      "07/29",
      "7/30",
      "07/30",
      "7/31",
      "07/31",
      "jul 29",
      "jul 30",
      "jul 31",
      "july 29",
      "july 30",
      "july 31",
      "aug",
      "8/",
  ]
  for f_pattern in future_date_patterns:
    if f_pattern in lower:
      return False

  if "today" in lower:
    return True

  current_date_patterns = [
      "7/28",
      "07/28",
      "7-28",
      "07-28",
      "jul 28",
      "july 28",
      "28 jul",
      "28 july",
  ]
  for pattern in current_date_patterns:
    if pattern in lower:
      return True
  return True


def are_same_game(g1, g2):
  try:
    g1 = g1.lower().strip()
    g2 = g2.lower().strip()
    if g1 == g2:
      return True

    prefixes = r"^(1st inning|1st half|5 innings|1st 5 innings|live)\s+"
    g1_clean = re.sub(prefixes, "", g1).strip()
    g2_clean = re.sub(prefixes, "", g2).strip()

    if g1_clean == g2_clean:
      return True

    g1_teams = re.split(r"\s+@\s+|\s+vs\.?\s+", g1_clean)
    g2_teams = re.split(r"\s+@\s+|\s+vs\.?\s+", g2_clean)

    if len(g1_teams) == 2 and len(g2_teams) == 2:

      def get_words(team_str):
        return set(re.findall(r"[a-z]{3,}", team_str))

      w1_t1, w1_t2 = get_words(g1_teams[0]), get_words(g1_teams[1])
      w2_t1, w2_t2 = get_words(g2_teams[0]), get_words(g2_teams[1])

      if (w1_t1 & w2_t1) and (w1_t2 & w2_t2):
        return True
      if (w1_t1 & w2_t2) and (w1_t2 & w2_t1):
        return True
  except Exception:
    pass
  return False


def is_player_prop(raw_text):
  lower = raw_text.lower()
  return "player" in lower or "to record" in lower or (
      "over" in lower
      and (
          "hits" in lower
          or "runs" in lower
          or "strikeouts" in lower
          or "bases" in lower
          or "rbis" in lower
          or "homers" in lower
          or "walks" in lower
          or "yards" in lower
          or "touchdowns" in lower
          or "points" in lower
          or "rebounds" in lower
          or "assists" in lower
      )
  )


def is_pitcher_strikeouts(raw_text):
  lower = raw_text.lower()
  return (
      "strikeout" in lower
      or "ks" in raw_text
      or ("so" in lower and "pitcher" in lower)
  )


# --- STREAMLIT UI SIDEBAR CONFIGURATION ---
st.sidebar.header("⚙️ Configuration")

target_league_input = st.sidebar.selectbox(
    "Target League", list(league_mapping.keys()), index=3, key="cfg_league"
)

# Dynamically filter market types based on selected sport/league
league_upper = target_league_input.upper()
if league_upper == "MLB":
  available_markets = [
      "Moneyline",
      "Run Line",
      "Total (Over/Under)",
      "Player Home Runs",
      "Player Total Bases",
      "Player Hits",
      "Player Pitching Strikeouts",
      "Player RBIs",
      "Player Runs",
      "Player Walks",
  ]
elif league_upper in ["NFL", "NCAAF"]:
  available_markets = [
      "Moneyline",
      "Point Spread",
      "Total (Over/Under)",
      "Player Touchdowns",
      "Player Passing Yards",
      "Player Rushing Yards",
      "Player Receiving Yards",
  ]
elif league_upper in ["NBA", "NCAAB", "WNBA"]:
  available_markets = [
      "Moneyline",
      "Point Spread",
      "Total (Over/Under)",
      "Player Points",
      "Player Rebounds",
      "Player Assists",
      "Player Threes",
  ]
elif league_upper == "NHL":
  available_markets = [
      "Moneyline",
      "Puck Line",
      "Total (Over/Under)",
      "Player Goals",
      "Player Assists",
      "Player Points",
      "Player Shots On Goal",
  ]
elif league_upper in ["FIFA", "LIGA MX", "MLS", "SERIE A"]:
  available_markets = [
      "Moneyline",
      "Goal Spread",
      "Total Goals",
      "Player Goals",
      "Player Shots On Target",
  ]
else:  # ALL or others
  available_markets = [
      "Moneyline",
      "Spread / Run Line / Puck Line",
      "Total (Over/Under)",
      "Player Home Runs",
      "Player Touchdowns",
      "Player Points",
      "Player Goals",
      "Player Pitching Strikeouts",
  ]

exclude_input = st.sidebar.text_input(
    "Global Teams to Exclude (comma-separated)", "", key="cfg_exclude"
)
excluded_teams = [
    t.strip().lower() for t in exclude_input.split(",") if t.strip()
]

include_input = st.sidebar.text_input(
    "Global Teams to Strictly Include (comma-separated)", "", key="cfg_include"
)
included_teams = [
    t.strip().lower() for t in include_input.split(",") if t.strip()
]

st.sidebar.markdown("---")
st.sidebar.subheader("Sportsbook Specifics")

# Select active books
selected_books = st.sidebar.multiselect(
    "Select Sportsbooks to Run",
    options=list(unique_books_dict.keys()),
    default=["DraftKings", "FanDuel"],
)

book_configs = {}

# Expanders for unique sportsbook parameters
for book in selected_books:
  with st.sidebar.expander(f"⚙️ {book} Settings", expanded=False):
    legs = st.number_input(
        f"Legs per Parlay",
        min_value=1,
        max_value=5,
        value=2,
        step=1,
        key=f"{book}_legs",
    )
    min_odds = st.number_input(f"Minimum Odds", value=400, key=f"{book}_min")
    max_odds = st.number_input(f"Maximum Odds", value=1000, key=f"{book}_max")
    boost = st.number_input(f"Profit Boost (%)", value=0.0, key=f"{book}_boost")

    # Sport-appropriate market type selector
    market_types = st.multiselect(
        f"Allowed Market Types",
        options=available_markets,
        default=[available_markets[0]],
        key=f"{book}_markets",
        help=f"Select specific bet markets for {target_league_input}.",
    )

    mainlines = st.checkbox(f"Mainlines Only", value=True, key=f"{book}_main")
    requires_under = st.checkbox(
        f"Requires 'Under'", value=False, key=f"{book}_under"
    )

    has_prop = any("Player" in m for m in market_types)
    if has_prop and mainlines:
      st.warning(
          "⚠️ You selected a Player Prop market but have 'Mainlines Only'"
          " checked. The scraper will automatically UNCHECK 'Mainlines Only' on"
          " the site to find these props."
      )

    book_configs[book] = {
        "legs": legs,
        "min_odds": min_odds,
        "max_odds": max_odds,
        "boost": boost,
        "mainlines": mainlines,
        "requires_under": requires_under,
        "market_types": market_types,
        "has_prop": has_prop,
    }

st.sidebar.markdown("---")
run_button = st.sidebar.button(
    "🚀 Run Scraper & Find Parlays", key="cfg_run_button", use_container_width=True
)

st.title("⚾ Multi-Bet Parlay Scraper")

if run_button:
  if not selected_books:
    st.error("Please select at least one sportsbook to run.")
    st.stop()

  saved_sessions = []

  for book_name in selected_books:
    cfg = book_configs[book_name]
    final_mainlines = (
        "n" if cfg["has_prop"] else ("y" if cfg["mainlines"] else "n")
    )

    saved_sessions.append({
        "book_val": unique_books_dict[book_name],
        "book_name": book_name.lower(),
        "target_league": target_league_input,
        "legs": cfg["legs"],
        "min_odds": cfg["min_odds"],
        "max_odds": cfg["max_odds"],
        "mainlines": final_mainlines,
        "requires_under": cfg["requires_under"],
        "market_types": cfg["market_types"],
        "boost": cfg["boost"],
        "excluded_teams": excluded_teams,
        "included_teams": included_teams,
        "num_results": 1,
    })

  progress_placeholder = st.empty()
  progress_placeholder.info("Launching Playwright scraper... Please wait.")


  # --- ASYNC SCRAPER RUNNER ---
  async def run_playwright_scraper():
    async with async_playwright() as p:
      browser = await p.chromium.launch(headless=True)
      context = await browser.new_context(
          user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
      )

      scraped_data_per_session = []
      all_scraped_games = {}

      for session in saved_sessions:
        page = await context.new_page()
        target_league = session["target_league"]

        await page.goto("https://crazyninjaodds.com/site/tools/positive-ev.aspx")

        # Set Sportsbook
        await page.locator(
            "#ContentPlaceHolderMain_ContentPlaceHolderRight_WebUserControl_FilterSportsbookSite_DropDownListSportsbookSite_All"
        ).select_option(session["book_val"])

        # Set League
        league_val = league_mapping.get(target_league.upper(), "0")
        league_locator = page.locator(
            "#ContentPlaceHolderMain_ContentPlaceHolderRight_WebUserControl_FilterLeague_DropDownListLeague"
        )
        await league_locator.select_option(league_val)
        await league_locator.evaluate(
            "element => element.dispatchEvent(new Event('change', { bubbles:"
            " true }))"
        )

        # Manage the Mainlines Only Checkbox
        mainline_checkbox = page.get_by_role("checkbox", name="Mainlines Only")
        if session["mainlines"] == "y":
          if not await mainline_checkbox.is_checked():
            await mainline_checkbox.check()
        else:
          if await mainline_checkbox.is_checked():
            await mainline_checkbox.uncheck()

        # Type market type into CNO's "Market Name Contains" input box
        market_query = ""
        if session["market_types"] and session["market_types"][0]:
          selected_m = session["market_types"][0]
          market_query = selected_m.replace("Player ", "")

        if market_query:
          try:
            market_input = page.locator("input").filter(
                has=page.locator(
                    "xpath=//ancestor::tr[contains(., 'Market Name"
                    " Contains')]//input"
                )
            ).first
            if not await market_input.count():
              market_input = page.locator(
                  "input[id*='MarketName'], input[id*='Market']"
              ).first
            if await market_input.count():
              await market_input.click()
              await market_input.fill(market_query)
              await market_input.evaluate(
                  "element => element.dispatchEvent(new Event('change', {"
                  " bubbles: true }))"
              )
          except Exception:
            pass

        await page.locator(
            "#ContentPlaceHolderMain_ContentPlaceHolderRight_TextBoxMinimumEVPercentage"
        ).click()
        await page.locator(
            "#ContentPlaceHolderMain_ContentPlaceHolderRight_TextBoxMinimumEVPercentage"
        ).fill("-99")
        await page.locator(
            "#ContentPlaceHolderMain_ContentPlaceHolderRight_WebUserControl_FilterDevigMethod_DropDownListDevigMethod"
        ).select_option("8")
        await page.locator(
            "#ContentPlaceHolderMain_ContentPlaceHolderRight_WebUserControl_FilterOddsProviderCount_TextBoxMinimumOddsProviderCount"
        ).click()
        await page.locator(
            "#ContentPlaceHolderMain_ContentPlaceHolderRight_WebUserControl_FilterOddsProviderCount_TextBoxMinimumOddsProviderCount"
        ).fill("5")

        await page.get_by_role("button", name="Update").click()

        try:
          await page.wait_for_selector("table tbody tr", timeout=5000)
        except Exception:
          pass

        await asyncio.sleep(2.5)

        results = page.locator("table tbody tr")
        total_rows = await results.count()

        app_plays = []
        for i in range(total_rows):
          row_element = results.nth(i)
          raw_text = await row_element.inner_text()
          clean_line = " | ".join([
              line.strip() for line in raw_text.splitlines() if line.strip()
          ])
          lower_text = clean_line.lower()

          if (
              target_league != "ALL"
              and target_league.lower() not in lower_text
          ):
            continue
          if not is_today_game(clean_line):
            continue
          if any(
              ex_team in lower_text for ex_team in session["excluded_teams"]
          ):
            continue
          if session["included_teams"] and not any(
              inc_team in lower_text for inc_team in session["included_teams"]
          ):
            continue

          ev_match = re.search(r"([-+]?[0-9]+\.[0-9]+)\s*%", clean_line)
          if ev_match:
            ev_value = float(ev_match.group(1))
            if ev_value <= -99.0:
              continue
          else:
            continue

          odds_match = re.findall(r"([+-]\d{3,4})", clean_line)
          if not odds_match:
            continue

          american_odds = int(odds_match[0])
          decimal_odds = american_to_decimal(american_odds)

          true_prob = (ev_value / 100.0 + 1.0) / decimal_odds
          true_dec = 1.0 / true_prob if true_prob > 0 else 2.0
          true_american = decimal_to_american(true_dec)

          game_id = "Unknown Game"
          split_pattern = fr'[A-Za-z]+\s+{target_league}\s+'
          sub_parts = re.split(split_pattern, clean_line, flags=re.IGNORECASE)

          if len(sub_parts) > 1:
            game_candidate = (
                sub_parts[1]
                .split("Player")[0]
                .split("Run Line")[0]
                .split("Total")[0]
                .split("Moneyline")[0]
                .split("Spread")[0]
                .strip()
            )
            if "@" in game_candidate or "vs" in game_candidate.lower():
              game_id = game_candidate

          if game_id == "Unknown Game":
            matchup_match = re.search(
                r"([A-Za-z\s]+)\s*(?:@|vs\.?)\s*([A-Za-z\s]+)",
                clean_line,
                re.IGNORECASE,
            )
            if matchup_match:
              full_matchup = matchup_match.group(0)
              words = full_matchup.split()
              generic_words = [
                  "baseball",
                  "football",
                  "basketball",
                  "hockey",
                  "soccer",
                  target_league.lower(),
                  "today",
                  "tomorrow",
                  "at",
                  "vs",
              ]
              clean_words = [
                  w for w in words if w.lower() not in generic_words
              ]
              if len(clean_words) >= 2:
                game_id = f"{clean_words[-2]} @ {clean_words[-1]}"

          play_obj = {
              "game": game_id,
              "odds": american_odds,
              "dec_odds": decimal_odds,
              "true_dec": true_dec,
              "true_american": true_american,
              "ev": ev_value,
              "is_under": "under" in lower_text,
              "is_prop": is_player_prop(clean_line),
              "is_k": is_pitcher_strikeouts(clean_line),
              "raw": clean_line,
          }
          app_plays.append(play_obj)
          if game_id != "Unknown Game":
            all_scraped_games[game_id] = True

        scraped_data_per_session.append(app_plays)
        await page.close()

      await browser.close()
      return scraped_data_per_session, all_scraped_games

  loop = asyncio.new_event_loop()
  asyncio.set_event_loop(loop)
  scraped_data_per_session, all_scraped_games = loop.run_until_complete(
      run_playwright_scraper()
  )
  loop.close()

  progress_placeholder.empty()
  st.success("Scraping and processing completed successfully!")

  # --- RENDER RESULTS ---
  all_global_parlays = []
  for s_idx, session in enumerate(saved_sessions):
    app_plays = scraped_data_per_session[s_idx]
    for combo in itertools.combinations(app_plays, session["legs"]):
      seen_games_in_combo = set()
      is_valid_combo = True
      for leg in combo:
        g = leg["game"]
        if any(are_same_game(eg, g) for eg in seen_games_in_combo):
          is_valid_combo = False
          break
        seen_games_in_combo.add(g)

      if not is_valid_combo:
        continue
      if session["requires_under"] and not any(
          leg["is_under"] for leg in combo
      ):
        continue

      combined_dec = 1.0
      combined_true_dec = 1.0
      combined_ev = 0.0
      for leg in combo:
        combined_dec *= leg["dec_odds"]
        combined_true_dec *= leg["true_dec"]
        combined_ev += leg["ev"]

      combined_american = decimal_to_american(combined_dec)
      combined_true_american = decimal_to_american(combined_true_dec)

      boost = session["boost"]
      if boost > 0:
        boosted_dec = 1.0 + (combined_dec - 1.0) * (1.0 + boost / 100.0)
        boosted_american = decimal_to_american(boosted_dec)
        boosted_ev = ((1.0 / combined_true_dec) * boosted_dec - 1.0) * 100.0
      else:
        boosted_dec = combined_dec
        boosted_american = combined_american
        boosted_ev = combined_ev

      min_dec_target = american_to_decimal(session["min_odds"])
      max_dec_target = american_to_decimal(session["max_odds"])
      in_range = min_dec_target <= combined_dec <= max_dec_target
      distance_from_range = (
          (min_dec_target - combined_dec)
          if combined_dec < min_dec_target
          else (
              (combined_dec - max_dec_target)
              if combined_dec > max_dec_target
              else 0.0
          )
      )

      all_global_parlays.append({
          "session_idx": s_idx,
          "legs": combo,
          "total_american": combined_american,
          "boosted_american": boosted_american,
          "total_true_american": combined_true_american,
          "standard_ev": combined_ev,
          "boosted_ev": boosted_ev,
          "in_range": in_range,
          "distance_from_range": distance_from_range,
          "combined_dec": combined_dec,
          "boosted_dec": boosted_dec,
      })

  all_global_parlays.sort(
      key=lambda x: (not x["in_range"], x["distance_from_range"], -x["boosted_ev"])
  )

  final_picks_by_session = {i: [] for i in range(len(saved_sessions))}
  global_used_games = {}

  for parlay in all_global_parlays:
    s_idx = parlay["session_idx"]
    session = saved_sessions[s_idx]
    if len(final_picks_by_session[s_idx]) >= session["num_results"]:
      continue

    overlap = False
    for leg in parlay["legs"]:
      g = leg["game"]
      for existing_g, info in global_used_games.items():
        if are_same_game(existing_g, g):
          if info["has_exclusive"] or ((not leg["is_prop"]) or leg["is_k"]):
            overlap = True
            break
      if overlap:
        break
    if overlap:
      continue

    final_picks_by_session[s_idx].append(parlay)
    for leg in parlay["legs"]:
      g = leg["game"]
      has_exc = (not leg["is_prop"]) or leg["is_k"]
      matched_key = next(
          (eg for eg in global_used_games if are_same_game(eg, g)), None
      )
      if matched_key:
        global_used_games[matched_key]["has_exclusive"] = (
            global_used_games[matched_key]["has_exclusive"] or has_exc
        )
      else:
        global_used_games[g] = {"has_exclusive": has_exc}

  total_mass_ev = 0.0
  total_mass_expected_profit = 0.0
  all_bet_details = []

  for s_idx, session in enumerate(saved_sessions):
    picks = final_picks_by_session[s_idx]
    if not picks:
      continue

    st.subheader(
        f"Results for {session['book_name'].upper()}"
        f" ({session['target_league']})"
    )
    for p_idx, parlay in enumerate(picks, 1):
      t_parlay_str = (
          f"+{parlay['total_true_american']}"
          if parlay["total_true_american"] > 0
          else str(parlay["total_true_american"])
      )
      boost_str = (
          f" | Boosted Odds: +{parlay['boosted_american']}"
          if session["boost"] > 0
          else ""
      )
      box_class = "parlay-box" if parlay["in_range"] else "parlay-box-out"
      header_class = (
          "parlay-header" if parlay["in_range"] else "parlay-header-out"
      )

      total_mass_ev += parlay["boosted_ev"]
      bet_exp = 10.0 * (parlay["boosted_ev"] / 100.0)
      total_mass_expected_profit += bet_exp
      net_profit_win = 10.0 * (parlay["boosted_dec"] - 1.0)
      p_win = (
          1.0 / parlay["combined_dec"] if parlay["combined_dec"] > 0 else 0.5
      )

      all_bet_details.append({
          "stake": 10.0,
          "net_profit": net_profit_win,
          "boosted_dec": parlay["boosted_dec"],
          "p_win": p_win,
      })

      html_card = f"""
            <div class="{box_class}">
                <div class="{header_class}">Parlay #{p_idx} | Odds: +{parlay['total_american']}{boost_str} (Fair: {t_parlay_str})</div>
                <div style="margin-bottom: 8px; color: #4CAF50;">
                    <strong>Standard EV:</strong> {parlay['standard_ev']:.2f}% &nbsp;|&nbsp; 
                    <strong>Boosted EV:</strong> {parlay['boosted_ev']:.2f}%
                </div>
                <div style="margin-top: 5px; font-weight: bold; color: #aaa;">Selections:</div>
            """
      for leg in parlay["legs"]:
        t_odd_str = (
            f"+{leg['true_american']}"
            if leg["true_american"] > 0
            else str(leg["true_american"])
        )
        html_card += (
            f'<div class="parlay-leg">• {leg["raw"]} <span style="color:'
            f' #888;">[True Odds: {t_odd_str}]</span></div>'
        )
      html_card += "</div>"
      st.markdown(html_card, unsafe_allow_html=True)

  # Mass Bet Portfolio Summary
  if len(all_bet_details) > 0:
    total_stakes = sum(b["stake"] for b in all_bet_details)
    num_bets = len(all_bet_details)
    total_outcomes = 2**num_bets
    winning_outcomes = 0
    prob_all_win, prob_all_lose = 1.0, 1.0

    for b in all_bet_details:
      prob_all_win *= b["p_win"]
      prob_all_lose *= 1.0 - b["p_win"]

    for i in range(total_outcomes):
      win_mask = [(i >> b_idx) & 1 == 1 for b_idx in range(num_bets)]
      profit = -total_stakes
      for idx, w in enumerate(win_mask):
        if w:
          profit += (
              all_bet_details[idx]["stake"] + all_bet_details[idx]["net_profit"]
          )
      if profit >= 0:
        winning_outcomes += 1

    breakeven_pct = (winning_outcomes / total_outcomes) * 100.0
    roi_percentage = (
        (total_mass_expected_profit / total_stakes) * 100.0
        if total_stakes > 0
        else 0.0
    )

    summary_html = f"""
<div style="background-color: #111; color: #f0f0f0; padding: 20px; border-radius: 8px; margin-top: 20px; font-family: monospace; border: 2px solid #4CAF50;">
    <h3 style="color: #4CAF50; margin-top: 0;">=== Mass Bet Summary ===</h3>
    <div><strong>Total Money Bet:</strong> ${total_stakes:.2f}</div>
    <div><strong>Total Combined EV:</strong> {total_mass_ev:.2f}%</div>
    <div><strong>Expected Profit:</strong> ${total_mass_expected_profit:.2f}</div>
    <div><strong>Portfolio ROI:</strong> {roi_percentage:.2f}%</div>
    <div><strong>Probability to Break Even:</strong> {breakeven_pct:.2f}%</div>
    <div><strong>Probability All Bets Win:</strong> {prob_all_win * 100:.2f}%</div>
    <div><strong>Probability All Bets Lose:</strong> {prob_all_lose * 100:.2f}%</div>
</div>
"""
    st.markdown(summary_html, unsafe_allow_html=True)

  # MLB Game Slate & Betting Status Tracking
  if target_league_input.upper() == "MLB" and all_scraped_games:
    st.markdown("### 🏟️ MLB Game Slate & Betting Status")
    bet_games_map = {}
    for s_idx, picks in final_picks_by_session.items():
      for parlay in picks:
        for leg in parlay["legs"]:
          bet_games_map[leg["game"]] = {
              "is_prop": leg["is_prop"],
              "market": leg["raw"],
          }

    for g_id in sorted(all_scraped_games.keys()):
      matched_bet = None
      is_prop_bet = False
      for bg, binfo in bet_games_map.items():
        if are_same_game(bg, g_id):
          matched_bet = bg
          is_prop_bet = binfo["is_prop"]
          break

      if matched_bet:
        prop_note = (
            " (Note: Bet placed was a Player Prop)"
            if is_prop_bet
            else " (Bet placed: Mainline/Team market)"
        )
        st.markdown(f"- ✅ **{g_id}**: Bet placed{prop_note}")
      else:
        st.markdown(f"- ⏳ **{g_id}**: Yet to be bet on")
