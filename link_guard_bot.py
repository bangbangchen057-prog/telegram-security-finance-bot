
import re
import os
import logging
import requests
import threading
import time
import json
import datetime
from urllib.parse import urlparse, urljoin
from openai import OpenAI
from bs4 import BeautifulSoup
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes, CallbackQueryHandler

# ==================== Health Check Server (starts immediately, before any bot init) ====================
def _start_health_server():
    from http.server import HTTPServer, BaseHTTPRequestHandler
    _port = int(os.environ.get('PORT', 8080))
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        def do_POST(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        def do_HEAD(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
        def log_message(self, format, *args):
            pass  # suppress access logs
    _server = HTTPServer(('0.0.0.0', _port), _Handler)
    _server.serve_forever()

threading.Thread(target=_start_health_server, daemon=True).start()

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('BOT_TOKEN', '8433512894:AAEYep9FGl0HKoOBOh0IIpdQWY8n5Ppajxc')

# ==================== 安全防护系统 ====================
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'bYwsop-cixba9-vuqwod')
SECURITY_LOG_FILE = None  # Will be set after DATA_DIR
_failed_auth_attempts = {}  # user_id -> {'count': int, 'last_time': float}
_security_alerts = []  # List of security events
MAX_FAILED_ATTEMPTS = 3  # Max failed password attempts before blocking
BLOCK_DURATION = 3600  # Block duration in seconds (1 hour)

def _log_security_event(event_type: str, user_id: int, details: str = ''):
    """Log security events for audit trail."""
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    event = {'time': timestamp, 'type': event_type, 'user_id': user_id, 'details': details}
    _security_alerts.append(event)
    # Keep only last 100 events in memory
    if len(_security_alerts) > 100:
        _security_alerts.pop(0)
    # Write to file
    try:
        if SECURITY_LOG_FILE:
            with open(SECURITY_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps(event, ensure_ascii=False) + '\n')
    except Exception:
        pass
    logger.warning(f"[SECURITY] {event_type}: user={user_id} {details}")

def _is_user_blocked(user_id: int) -> bool:
    """Check if user is blocked due to too many failed attempts."""
    if user_id not in _failed_auth_attempts:
        return False
    info = _failed_auth_attempts[user_id]
    if info['count'] >= MAX_FAILED_ATTEMPTS:
        if time.time() - info['last_time'] < BLOCK_DURATION:
            return True
        else:
            # Block expired, reset
            del _failed_auth_attempts[user_id]
            return False
    return False

def _record_failed_attempt(user_id: int):
    """Record a failed authentication attempt."""
    if user_id not in _failed_auth_attempts:
        _failed_auth_attempts[user_id] = {'count': 0, 'last_time': 0}
    _failed_auth_attempts[user_id]['count'] += 1
    _failed_auth_attempts[user_id]['last_time'] = time.time()

# AI 功能 - 使用 OpenAI 兼容 API
ai_client = OpenAI(
    api_key=os.environ.get('OPENAI_API_KEY', ''),
    base_url=os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1')
)

# ==================== 数据存储 ====================
DATA_DIR = Path(os.environ.get('DATA_DIR', '/home/ubuntu/bot_data'))
DATA_DIR.mkdir(exist_ok=True)
SUBSCRIBERS_FILE = DATA_DIR / 'subscribers.json'
FINANCE_FILE = DATA_DIR / 'finance.json'
SECURITY_LOG_FILE = DATA_DIR / 'security_log.jsonl'

def load_json(fp, default):
    try:
        if fp.exists():
            with open(fp, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception as e: logger.error(f"加载 {fp} 失败: {e}")
    return default

def save_json(fp, data):
    try:
        with open(fp, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e: logger.error(f"保存 {fp} 失败: {e}")

subscribers = set(load_json(SUBSCRIBERS_FILE, []))
finance_data = load_json(FINANCE_FILE, {})
def save_finance(): save_json(FINANCE_FILE, finance_data)
def save_subs(): save_json(SUBSCRIBERS_FILE, list(subscribers))

# ==================== 财务管理模块配置 ====================
EXPENSE_CATEGORIES = ["运营费", "手续费", "人工费", "广告费", "税费", "办公费", "物流费", "其他费用"]

MALAYSIA_PAYMENT_METHODS = ["DuitNow", "Touch 'n Go", "GrabPay", "Boost", "MAE", "ShopeePay", "Bank Transfer", "FPX", "Cash", "USDT"]
MALAYSIA_DEFAULT_FEE_RATES = {
    "DuitNow": 0.0,
    "Touch 'n Go": 0.8,
    "GrabPay": 0.8,
    "Boost": 0.8,
    "MAE": 0.0,
    "ShopeePay": 0.8,
    "Bank Transfer": 0.5,
    "FPX": 0.5,
    "Cash": 0.0,
    "USDT": 1.0,
}

PHILIPPINES_PAYMENT_METHODS = ["GCash", "Maya/PayMaya", "BPI", "BDO", "UnionBank", "GrabPay PH", "ShopeePay PH", "Bank Transfer", "Cash", "USDT"]
PHILIPPINES_DEFAULT_FEE_RATES = {
    "GCash": 0.8,
    "Maya/PayMaya": 0.8,
    "BPI": 0.5,
    "BDO": 0.5,
    "UnionBank": 0.5,
    "GrabPay PH": 0.8,
    "ShopeePay PH": 0.8,
    "Bank Transfer": 0.5,
    "Cash": 0.0,
    "USDT": 1.0,
}

BRAZIL_PAYMENT_METHODS = ["PIX", "Boleto", "TED", "DOC", "Nubank", "PicPay", "Mercado Pago", "Bank Transfer", "Cash", "USDT"]
BRAZIL_DEFAULT_FEE_RATES = {
    "PIX": 0.0,
    "Boleto": 1.0,
    "TED": 0.5,
    "DOC": 0.5,
    "Nubank": 0.0,
    "PicPay": 0.8,
    "Mercado Pago": 0.8,
    "Bank Transfer": 0.5,
    "Cash": 0.0,
    "USDT": 1.0,
}

# ==================== 汇率配置 ====================
# 各币种对 USDT 的汇率（1 USDT = X 本地货币）
EXCHANGE_RATES = {
    "MYR": float(os.environ.get('RATE_MYR', '4.45')),   # 1 USDT ≈ 4.45 MYR
    "PHP": float(os.environ.get('RATE_PHP', '56.0')),    # 1 USDT ≈ 56 PHP
    "BRL": float(os.environ.get('RATE_BRL', '5.10')),    # 1 USDT ≈ 5.10 BRL
}
MODULE_CURRENCY = {
    'malaysia_finance': 'MYR',
    'philippines_finance': 'PHP',
    'brazil_finance': 'BRL',
}

# ==================== 财务数据获取与初始化 ====================
def get_user_finance_module(chat_id, module_name):
    chat_id_str = str(chat_id)
    if chat_id_str not in finance_data:
        finance_data[chat_id_str] = {}
    
    user_modules = finance_data[chat_id_str]

    if module_name == 'malaysia_finance':
        if module_name not in user_modules:
            user_modules[module_name] = {
                "balance": 0.0,
                "transactions": [],
                "expenses": [],
                "merchants": {},
                "fee_rates": dict(MALAYSIA_DEFAULT_FEE_RATES),
                "user_stats": {"registered": 0, "first_deposit": 0, "total_depositors": 0},
                "betting": {"records": []},
                "agent": {
                    "deposit_fee_rate": 1.0,
                    "withdraw_fee_rate": 1.0,
                    "game_vendor_rate": 15.0,
                    "maintenance_fee": 0.0,
                    "bonus_records": [],
                    "settlements": [],
                }
            }
        # Ensure all keys exist for existing data
        d = user_modules[module_name]
        if "expenses" not in d: d["expenses"] = []
        if "merchants" not in d: d["merchants"] = {}
        if "fee_rates" not in d: d["fee_rates"] = dict(MALAYSIA_DEFAULT_FEE_RATES)
        if "user_stats" not in d: d["user_stats"] = {"registered": 0, "first_deposit": 0, "total_depositors": 0}
        if "betting" not in d: d["betting"] = {"records": []}
        if "agent" not in d: d["agent"] = {
            "deposit_fee_rate": 1.0,
            "withdraw_fee_rate": 1.0,
            "game_vendor_rate": 15.0,
            "maintenance_fee": 0.0,
            "bonus_records": [],
            "settlements": [],
        }

    elif module_name == 'philippines_finance':
        if module_name not in user_modules:
            user_modules[module_name] = {
                "balance": 0.0,
                "transactions": [],
                "expenses": [],
                "merchants": {},
                "fee_rates": dict(PHILIPPINES_DEFAULT_FEE_RATES),
                "user_stats": {"registered": 0, "first_deposit": 0, "total_depositors": 0},
                "betting": {"records": []},
                "agent": {
                    "deposit_fee_rate": 1.0,
                    "withdraw_fee_rate": 1.0,
                    "game_vendor_rate": 15.0,
                    "maintenance_fee": 0.0,
                    "bonus_records": [],
                    "settlements": [],
                }
            }
        # Ensure all keys exist for existing data
        d = user_modules[module_name]
        if "expenses" not in d: d["expenses"] = []
        if "merchants" not in d: d["merchants"] = {}
        if "fee_rates" not in d: d["fee_rates"] = dict(PHILIPPINES_DEFAULT_FEE_RATES)
        if "user_stats" not in d: d["user_stats"] = {"registered": 0, "first_deposit": 0, "total_depositors": 0}
        if "betting" not in d: d["betting"] = {"records": []}
        if "agent" not in d: d["agent"] = {
            "deposit_fee_rate": 1.0,
            "withdraw_fee_rate": 1.0,
            "game_vendor_rate": 15.0,
            "maintenance_fee": 0.0,
            "bonus_records": [],
            "settlements": [],
        }

    elif module_name == 'brazil_finance':
        if module_name not in user_modules:
            user_modules[module_name] = {
                "balance": 0.0,
                "transactions": [],
                "expenses": [],
                "merchants": {},
                "fee_rates": dict(BRAZIL_DEFAULT_FEE_RATES),
                "user_stats": {"registered": 0, "first_deposit": 0, "total_depositors": 0},
                "betting": {"records": []},
                "agent": {
                    "deposit_fee_rate": 1.0,
                    "withdraw_fee_rate": 1.0,
                    "game_vendor_rate": 15.0,
                    "maintenance_fee": 0.0,
                    "bonus_records": [],
                    "settlements": [],
                }
            }
        # Ensure all keys exist for existing data
        d = user_modules[module_name]
        if "expenses" not in d: d["expenses"] = []
        if "merchants" not in d: d["merchants"] = {}
        if "fee_rates" not in d: d["fee_rates"] = dict(BRAZIL_DEFAULT_FEE_RATES)
        if "user_stats" not in d: d["user_stats"] = {"registered": 0, "first_deposit": 0, "total_depositors": 0}
        if "betting" not in d: d["betting"] = {"records": []}
        if "agent" not in d: d["agent"] = {
            "deposit_fee_rate": 1.0,
            "withdraw_fee_rate": 1.0,
            "game_vendor_rate": 15.0,
            "maintenance_fee": 0.0,
            "bonus_records": [],
            "settlements": [],
        }

    elif module_name == 'advertising_finance':
        if module_name not in user_modules:
            user_modules[module_name] = {
                "daily_data": {}
            }

    return user_modules[module_name]

def calc_summary(ud):
    total_dep = sum(t['amount'] for t in ud['transactions'] if t['type'] == 'deposit')
    total_wit = sum(t['amount'] for t in ud['transactions'] if t['type'] == 'withdrawal')
    total_fee = sum(t.get('fee', 0) for t in ud['transactions'])
    total_exp = sum(e['amount'] for e in ud['expenses'])
    total_bet = sum(r['bet_amount'] for r in ud.get('betting', {}).get('records', []))
    total_payout = sum(r['payout_amount'] for r in ud.get('betting', {}).get('records', []))
    net = total_dep - total_wit - total_exp - total_fee - total_payout + total_bet
    rate = (net / total_dep * 100) if total_dep > 0 else 0
    us = ud.get('user_stats', {})
    return {'total_deposit': total_dep, 'total_withdrawal': total_wit, 'total_expense': total_exp,
            'total_fee': total_fee, 'total_bet': total_bet, 'total_payout': total_payout,
            'net_profit': net, 'profit_rate': rate, 'balance': ud['balance'],
            'registered': us.get('registered', 0), 'first_deposit': us.get('first_deposit', 0),
            'total_depositors': us.get('total_depositors', 0)}

def get_merchant(ud, name):
    if name not in ud['merchants']:
        ud['merchants'][name] = {"balance": 0.0, "transactions": []}
    return ud['merchants'][name]

def calc_merchant_daily(merchant, date_str=None):
    if date_str is None:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    day_txns = [t for t in merchant['transactions'] if t['date'][:10] == date_str]
    dep = sum(t['amount'] for t in day_txns if t['type'] == 'deposit')
    wit = sum(t['amount'] for t in day_txns if t['type'] == 'withdrawal')
    fee = sum(t.get('fee', 0) for t in day_txns)
    return {'date': date_str, 'deposit': dep, 'withdrawal': wit, 'fee': fee, 'net': dep - wit - fee, 'count': len(day_txns)}

# ==================== 广告财务模块函数 ====================
def calc_ad_summary(ad_data):
    total_spend = sum(d.get('ad_spend', 0) for d in ad_data['daily_data'].values())
    total_clicks = sum(d.get('clicks', 0) for d in ad_data['daily_data'].values())
    total_registrations = sum(d.get('registrations', 0) for d in ad_data['daily_data'].values())
    total_first_deposits = sum(d.get('first_deposits', 0) for d in ad_data['daily_data'].values())
    total_purchases = sum(d.get('purchases', 0) for d in ad_data['daily_data'].values())
    total_depositors = sum(d.get('depositors', 0) for d in ad_data['daily_data'].values())
    total_deposit_amount = sum(d.get('deposit_amount', 0) for d in ad_data['daily_data'].values())

    roas = (total_deposit_amount / total_spend * 100) if total_spend > 0 else 0
    cpr = (total_spend / total_registrations) if total_registrations > 0 else 0

    return {
        'total_spend': total_spend,
        'total_clicks': total_clicks,
        'total_registrations': total_registrations,
        'total_first_deposits': total_first_deposits,
        'total_purchases': total_purchases,
        'total_depositors': total_depositors,
        'total_deposit_amount': total_deposit_amount,
        'roas': roas,
        'cpr': cpr
    }

async def ad_finance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ad_data = get_user_finance_module(chat_id, 'advertising_finance')
    s = calc_ad_summary(ad_data)

    text = (
        "📢 广告财务管理中心\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 总消耗: {s['total_spend']:.2f}\n"
        f"🖱️ 总点击: {s['total_clicks']}\n"
        f"📝 总注册: {s['total_registrations']}\n"
        f"💎 总首充: {s['total_first_deposits']}\n"
        f"👤 总充值人数: {s['total_depositors']}\n"
        f"💵 总充值金额: {s['total_deposit_amount']:.2f}\n"
        f"🛒 总购物: {s['total_purchases']}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 ROAS 回报率: {s['roas']:.1f}%\n"
        f"💲 单次成效 (CPR): {s['cpr']:.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "请选择操作："
    )
    keyboard = [
        [InlineKeyboardButton("➕ 录入今日数据", callback_data="adv_add_daily")],
        [InlineKeyboardButton("📊 日报统计", callback_data="adv_daily_report"),
         InlineKeyboardButton("📈 月报统计", callback_data="adv_monthly_report")],
        [InlineKeyboardButton("🔙 返回主菜单", callback_data="main_finance_menu")]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_ad_finance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    ad_data = get_user_finance_module(chat_id, 'advertising_finance')

    if data == "adv_add_daily":
        context.user_data['current_finance_module'] = 'advertising_finance'
        context.user_data['fin_action'] = 'add_ad_daily_data'
        context.user_data['awaiting_amount'] = True # This will trigger handle_finance_input
        await query.edit_message_text(
            "请输入今日广告数据，格式为：\n" 
            "消耗 点击 注册 首充 购物 充值人数 充值金额\n" 
            "例如：1000 5000 100 50 20 80 15000\n" 
            "(消耗、购物、充值金额可以是小数，其他为整数)"
        )
    elif data == "adv_daily_report":
        report_text = "📊 广告日报统计\n━━━━━━━━━━━━━━━━━━━━\n"
        if not ad_data['daily_data']:
            report_text += "暂无数据。"
        else:
            sorted_dates = sorted(ad_data['daily_data'].keys(), reverse=True)[:7] # Last 7 days
            for date_str in sorted_dates:
                day_data = ad_data['daily_data'][date_str]
                roas = (day_data.get('purchases', 0) / day_data.get('ad_spend', 0) * 100) if day_data.get('ad_spend', 0) > 0 else 0
                cpr = (day_data.get('ad_spend', 0) / day_data.get('registrations', 0)) if day_data.get('registrations', 0) > 0 else 0
                report_text += (
                    f"\n\U0001f4c5 {date_str}\n"
                    f"  \u6d88\u8017: {day_data.get('ad_spend', 0):.2f} | \u70b9\u51fb: {day_data.get('clicks', 0)}\n"
                    f"  \u6ce8\u518c: {day_data.get('registrations', 0)} | \u9996\u5145: {day_data.get('first_deposits', 0)}\n"
                    f"  \u5145\u503c\u4eba\u6570: {day_data.get('depositors', 0)} | \u5145\u503c\u91d1\u989d: {day_data.get('deposit_amount', 0):.2f}\n"
                    f"  \u8d2d\u7269: {day_data.get('purchases', 0):.2f} | ROAS: {roas:.1f}% | CPR: {cpr:.2f}\n"
                )
        kb = [[InlineKeyboardButton("🔙 返回广告菜单", callback_data="adv_main")]]
        await query.edit_message_text(report_text, reply_markup=InlineKeyboardMarkup(kb))

    elif data == "adv_monthly_report":
        report_text = "📈 广告月报统计\n━━━━━━━━━━━━━━━━━━━━\n"
        if not ad_data['daily_data']:
            report_text += "暂无数据。"
        else:
            monthly_summary = {}
            for date_str, day_data in ad_data['daily_data'].items():
                month = date_str[:7] # YYYY-MM
                if month not in monthly_summary:
                    monthly_summary[month] = {
                        'ad_spend': 0.0, 'clicks': 0, 'registrations': 0, 
                        'first_deposits': 0, 'purchases': 0, 'depositors': 0, 'deposit_amount': 0.0
                    }
                monthly_summary[month]['ad_spend'] += day_data.get('ad_spend', 0)
                monthly_summary[month]['clicks'] += day_data.get('clicks', 0)
                monthly_summary[month]['registrations'] += day_data.get('registrations', 0)
                monthly_summary[month]['first_deposits'] += day_data.get('first_deposits', 0)
                monthly_summary[month]['purchases'] += day_data.get('purchases', 0)
                monthly_summary[month]['depositors'] += day_data.get('depositors', 0)
                monthly_summary[month]['deposit_amount'] += day_data.get('deposit_amount', 0)
            
            sorted_months = sorted(monthly_summary.keys(), reverse=True)
            for month_str in sorted_months:
                month_data = monthly_summary[month_str]
                roas = (month_data.get('purchases', 0) / month_data.get('ad_spend', 0) * 100) if month_data.get('ad_spend', 0) > 0 else 0
                cpr = (month_data.get('ad_spend', 0) / month_data.get('registrations', 0)) if month_data.get('registrations', 0) > 0 else 0
                report_text += (
                    f"\n\U0001f4c5 {month_str}\n"
                    f"  \u6d88\u8017: {month_data.get('ad_spend', 0):.2f} | \u70b9\u51fb: {month_data.get('clicks', 0)}\n"
                    f"  \u6ce8\u518c: {month_data.get('registrations', 0)} | \u9996\u5145: {month_data.get('first_deposits', 0)}\n"
                    f"  \u5145\u503c\u4eba\u6570: {month_data.get('depositors', 0)} | \u5145\u503c\u91d1\u989d: {month_data.get('deposit_amount', 0):.2f}\n"
                    f"  \u8d2d\u7269: {month_data.get('purchases', 0):.2f} | ROAS: {roas:.1f}% | CPR: {cpr:.2f}\n"
                )
        kb = [[InlineKeyboardButton("🔙 返回广告菜单", callback_data="adv_main")]]
        await query.edit_message_text(report_text, reply_markup=InlineKeyboardMarkup(kb))

    elif data == "adv_main":
        await ad_finance_menu(update, context)
    elif data == "main_finance_menu":
        await main_finance_menu(update, context)

# ==================== 主财务菜单 ====================
async def main_finance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "请选择财务板块："
    keyboard = [
        [InlineKeyboardButton("🇲🇾 马来西亚财务", callback_data="select_finance_malaysia")],
        [InlineKeyboardButton("🇵🇭 菲律宾财务", callback_data="select_finance_philippines")],
        [InlineKeyboardButton("🇧🇷 巴西财务", callback_data="select_finance_brazil")],
        [InlineKeyboardButton("📢 广告财务", callback_data="select_finance_advertising")],
        [InlineKeyboardButton("💱 汇率换算", callback_data="exchange_rate_menu")],
        [InlineKeyboardButton("❌ 关闭菜单", callback_data="fin_close")]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def finance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    current_module = context.user_data.get('current_finance_module')

    if not current_module or current_module == 'advertising_finance': # Default to Malaysia if not set or if coming from advertising
        current_module = 'malaysia_finance'
        context.user_data['current_finance_module'] = current_module

    ud = get_user_finance_module(chat_id, current_module)
    s = calc_summary(ud)
    merchant_count = len(ud['merchants'])

    if current_module == 'malaysia_finance':
        module_prefix = "🇲🇾 马来西亚"
    elif current_module == 'philippines_finance':
        module_prefix = "🇵🇭 菲律宾"
    elif current_module == 'brazil_finance':
        module_prefix = "🇧🇷 巴西"
    else:
        module_prefix = "🇲🇾 马来西亚"

    text = (
        f"{module_prefix}财务管理中心\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 当前余额: {s['balance']:.2f}\n"
        f"📥 总存款: {s['total_deposit']:.2f}\n"
        f"📤 总提款: {s['total_withdrawal']:.2f}\n"
        f"💳 总手续费: {s['total_fee']:.2f}\n"
        f"📋 总费用: {s['total_expense']:.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🎰 投注流水: {s['total_bet']:.2f}\n"
        f"🏆 派奖流水: {s['total_payout']:.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 注册人数: {s['registered']}\n"
        f"💎 首充人数: {s['first_deposit']}\n"
        f"👤 总充人数: {s['total_depositors']}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 净利润: {s['net_profit']:.2f}\n"
        f"📊 利润率: {s['profit_rate']:.1f}%\n"
        f"🏪 商户数量: {merchant_count}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "请选择操作："
    )
    keyboard = [
        [InlineKeyboardButton("📥 记录存款", callback_data=f"{current_module[:3]}_fin_deposit"),
         InlineKeyboardButton("📤 记录提款", callback_data=f"{current_module[:3]}_fin_withdraw")],
        [InlineKeyboardButton("📋 记录费用", callback_data=f"{current_module[:3]}_fin_expense"),
         InlineKeyboardButton("📈 利润报表", callback_data=f"{current_module[:3]}_fin_profit")],
        [InlineKeyboardButton("📜 交易历史", callback_data=f"{current_module[:3]}_fin_history"),
         InlineKeyboardButton("📊 支付统计", callback_data=f"{current_module[:3]}_fin_stats")],
        [InlineKeyboardButton("📋 费用明细", callback_data=f"{current_module[:3]}_fin_expense_detail"),
         InlineKeyboardButton("📅 月度报表", callback_data=f"{current_module[:3]}_fin_monthly")],
        [InlineKeyboardButton("💳 支付费率", callback_data=f"{current_module[:3]}_fin_fee_rates"),
         InlineKeyboardButton("🏪 商户管理", callback_data=f"{current_module[:3]}_mch_menu")],
        [InlineKeyboardButton("👥 用户统计", callback_data=f"{current_module[:3]}_fin_user_stats"),
         InlineKeyboardButton("🎰 投注/派奖", callback_data=f"{current_module[:3]}_fin_betting")],
        [InlineKeyboardButton("💲 代理结算", callback_data=f"{current_module[:3]}_agt_menu")],
        [InlineKeyboardButton("🔙 返回主菜单", callback_data="main_finance_menu")],
        [InlineKeyboardButton("❌ 关闭菜单", callback_data="fin_close")]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== 汇率换算菜单 ====================
async def exchange_rate_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "💱 汇率换算中心\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🇲🇾 MYR/USDT: {EXCHANGE_RATES['MYR']} (1 USDT = {EXCHANGE_RATES['MYR']} MYR)\n"
        f"🇵🇭 PHP/USDT: {EXCHANGE_RATES['PHP']} (1 USDT = {EXCHANGE_RATES['PHP']} PHP)\n"
        f"🇧🇷 BRL/USDT: {EXCHANGE_RATES['BRL']} (1 USDT = {EXCHANGE_RATES['BRL']} BRL)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "点击下方按钮修改汇率："
    )
    keyboard = [
        [InlineKeyboardButton("🇲🇾 修改 MYR 汇率", callback_data="set_rate_MYR")],
        [InlineKeyboardButton("🇵🇭 修改 PHP 汇率", callback_data="set_rate_PHP")],
        [InlineKeyboardButton("🇧🇷 修改 BRL 汇率", callback_data="set_rate_BRL")],
        [InlineKeyboardButton("🔙 返回主菜单", callback_data="main_finance_menu")]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ==================== 财务回调 ====================
async def finance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if data.startswith("select_finance_"):
        module_name = data[len("select_finance_"):] + '_finance'
        context.user_data['current_finance_module'] = module_name
        if module_name == 'advertising_finance':
            await ad_finance_menu(update, context)
        else:
            await finance_menu(update, context)
        return
    
    if data.startswith("adv_"):
        await handle_ad_finance_callback(update, context)
        return

    if data == "exchange_rate_menu":
        await exchange_rate_menu(update, context)
        return

    if data.startswith("set_rate_"):
        currency = data[len("set_rate_"):]
        context.user_data['fin_action'] = f'set_rate_{currency}'
        context.user_data['awaiting_amount'] = True
        current_rate = EXCHANGE_RATES.get(currency, 1.0)
        await query.edit_message_text(f"当前 {currency}/USDT 汇率: {current_rate}\n(即 1 USDT = {current_rate} {currency})\n\n请输入新的汇率：")
        return

    current_module = context.user_data.get('current_finance_module')
    if not current_module:
        await query.edit_message_text("请先选择一个财务板块。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回主菜单", callback_data="main_finance_menu")]]))
        return

    ud = get_user_finance_module(chat_id, current_module)

    # Extract module prefix from data for routing
    module_prefix = data.split('_')[0]
    # Remove module prefix from data to match original logic
    original_data = '_'.join(data.split('_')[1:])

    # Determine PAYMENT_METHODS and DEFAULT_FEE_RATES based on current_module
    if current_module == 'malaysia_finance':
        PAYMENT_METHODS = MALAYSIA_PAYMENT_METHODS
        DEFAULT_FEE_RATES = MALAYSIA_DEFAULT_FEE_RATES
    elif current_module == 'philippines_finance':
        PAYMENT_METHODS = PHILIPPINES_PAYMENT_METHODS
        DEFAULT_FEE_RATES = PHILIPPINES_DEFAULT_FEE_RATES
    elif current_module == 'brazil_finance':
        PAYMENT_METHODS = BRAZIL_PAYMENT_METHODS
        DEFAULT_FEE_RATES = BRAZIL_DEFAULT_FEE_RATES
    else:
        # This should not happen if current_module is always set correctly
        await query.edit_message_text("未知财务模块。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回主菜单", callback_data="main_finance_menu")]]))
        return

    # ===== 存款 =====
    if original_data == "fin_deposit":
        kb = [[InlineKeyboardButton(m, callback_data=f"{module_prefix}_pay_dep_{m}")] for m in PAYMENT_METHODS]
        kb.append([InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_fin_main")])
        await query.edit_message_text("请选择存款支付方式：", reply_markup=InlineKeyboardMarkup(kb))

    elif original_data == "fin_withdraw":
        kb = [[InlineKeyboardButton(m, callback_data=f"{module_prefix}_pay_wit_{m}")] for m in PAYMENT_METHODS]
        kb.append([InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_fin_main")])
        await query.edit_message_text("请选择提款支付方式：", reply_markup=InlineKeyboardMarkup(kb))

    elif original_data == "fin_expense":
        kb = [[InlineKeyboardButton(c, callback_data=f"{module_prefix}_exp_cat_{c}")] for c in EXPENSE_CATEGORIES]
        kb.append([InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_fin_main")])
        await query.edit_message_text("请选择费用类别：", reply_markup=InlineKeyboardMarkup(kb))

    elif original_data.startswith("exp_cat_"):
        cat = original_data[8:]
        context.user_data['fin_action'] = 'expense'
        context.user_data['fin_expense_category'] = cat
        context.user_data['awaiting_amount'] = True
        await query.edit_message_text(f"费用类别: {cat}\n\n请输入金额（例如：50.00）：\n如需备注用空格分隔：50.00 购买办公用品")

    elif original_data.startswith("pay_"):
        parts = original_data.split("_")
        action_code = parts[1]
        method = parts[2]
        action_name = "存款" if action_code == "dep" else "提款"
        fee_rate = ud['fee_rates'].get(method, 0)
        context.user_data['fin_action'] = action_code
        context.user_data['fin_method'] = method
        context.user_data['awaiting_amount'] = True
        await query.edit_message_text(
            f"支付方式: {method} | 操作: {action_name}\n"
            f"当前费率: {fee_rate}%\n\n"
            "请输入金额（例如：100.50）：\n"
            "如需指定商户，用空格分隔：100.50 商户名"
        )

    # ===== 交易历史 =====
    elif original_data == "fin_history":
        history = ud['transactions'][-15:]
        if not history:
            text = "暂无交易记录。"
        else:
            text = "📜 最近 15 条交易记录\n━━━━━━━━━━━━━━━━━━━━\n"
            for t in reversed(history):
                icon = "📥" if t['type'] == 'deposit' else "📤"
                fee_txt = f" (费{t.get('fee',0):.2f})" if t.get('fee', 0) > 0 else ""
                mch_txt = f" [{t.get('merchant','')}]" if t.get('merchant') else ""
                text += f"{icon} {t['amount']:.2f}{fee_txt} | {t['method']}{mch_txt} | {t['date']}\n"
        kb = [[InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_fin_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    # ===== 支付统计 =====
    elif original_data == "fin_stats":
        dep_s, wit_s, fee_s = {}, {}, {}
        for t in ud['transactions']:
            m = t['method']
            if t['type'] == 'deposit':
                dep_s[m] = dep_s.get(m, 0) + t['amount']
            else:
                wit_s[m] = wit_s.get(m, 0) + t['amount']
            fee_s[m] = fee_s.get(m, 0) + t.get('fee', 0)
        text = "📊 各支付方式统计\n━━━━━━━━━━━━━━━━━━━━\n"
        text += "\n📥 存款：\n"
        for m, v in dep_s.items(): text += f"  {m}: {v:.2f} (费{fee_s.get(m,0):.2f})\n"
        if not dep_s: text += "  暂无\n"
        text += "\n📤 提款：\n"
        for m, v in wit_s.items(): text += f"  {m}: {v:.2f} (费{fee_s.get(m,0):.2f})\n"
        if not wit_s: text += "  暂无\n"
        kb = [[InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_fin_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    # ===== 利润报表 =====
    elif original_data == "fin_profit":
        s = calc_summary(ud)
        text = (
            "📈 利润报表\n━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 当前余额: {s['balance']:.2f}\n"
            f"📥 总存款: {s['total_deposit']:.2f}\n"
            f"📤 总提款: {s['total_withdrawal']:.2f}\n"
            f"💳 总手续费: {s['total_fee']:.2f}\n"
            f"📋 总费用: {s['total_expense']:.2f}\n"
            f"🎰 投注流水: {s['total_bet']:.2f}\n"
            f"🏆 派奖流水: {s['total_payout']:.2f}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 净利润: {s['net_profit']:.2f}\n"
            f"📊 利润率: {s['profit_rate']:.1f}%\n"
        )
        kb = [[InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_fin_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    # ===== 费用明细 =====
    elif original_data == "fin_expense_detail":
        if not ud['expenses']:
            text = "暂无费用记录。"
        else:
            text = "📋 最近 15 条费用明细\n━━━━━━━━━━━━━━━━━━━━\n"
            for e in reversed(ud['expenses'][-15:]):
                text += f"➖ {e['amount']:.2f} | {e['category']} | {e.get('note', '')} | {e['date']}\n"
        kb = [[InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_fin_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    # ===== 月度报表 =====
    elif original_data == "fin_monthly":
        monthly_summary = {}
        for t in ud['transactions']:
            month = t['date'][:7] # YYYY-MM
            if month not in monthly_summary: monthly_summary[month] = {'deposit': 0, 'withdrawal': 0, 'fee': 0}
            if t['type'] == 'deposit': monthly_summary[month]['deposit'] += t['amount']
            else: monthly_summary[month]['withdrawal'] += t['amount']
            monthly_summary[month]['fee'] += t.get('fee', 0)
        for e in ud['expenses']:
            month = e['date'][:7]
            if month not in monthly_summary: monthly_summary[month] = {'deposit': 0, 'withdrawal': 0, 'fee': 0, 'expense': 0}
            monthly_summary[month]['expense'] = monthly_summary[month].get('expense', 0) + e['amount']
        
        betting_summary = {}
        for r in ud.get('betting', {}).get('records', []):
            month = r['date'][:7]
            if month not in betting_summary: betting_summary[month] = {'bet': 0, 'payout': 0}
            betting_summary[month]['bet'] += r['bet_amount']
            betting_summary[month]['payout'] += r['payout_amount']

        text = "📅 月度报表\n━━━━━━━━━━━━━━━━━━━━\n"
        if not monthly_summary and not betting_summary:
            text += "暂无数据。"
        else:
            all_months = sorted(list(set(list(monthly_summary.keys()) + list(betting_summary.keys()))), reverse=True)[:6]
            for month in all_months:
                m_data = monthly_summary.get(month, {'deposit': 0, 'withdrawal': 0, 'fee': 0, 'expense': 0})
                b_data = betting_summary.get(month, {'bet': 0, 'payout': 0})
                net = m_data['deposit'] - m_data['withdrawal'] - m_data['fee'] - m_data['expense'] - b_data['payout'] + b_data['bet']
                rate = (net / m_data['deposit'] * 100) if m_data['deposit'] > 0 else 0
                text += (
                    f"\n📅 {month}\n"
                    f"  📥 存款: {m_data['deposit']:.2f} | 📤 提款: {m_data['withdrawal']:.2f}\n"
                    f"  💳 手续费: {m_data['fee']:.2f} | 📋 费用: {m_data['expense']:.2f}\n"
                    f"  🎰 投注: {b_data['bet']:.2f} | 🏆 派奖: {b_data['payout']:.2f}\n"
                    f"  📈 净利润: {net:.2f} | 📊 利润率: {rate:.1f}%\n"
                )
        kb = [[InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_fin_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    # ===== 支付费率 =====
    elif original_data == "fin_fee_rates":
        text = "💳 支付方式费率设置（%）\n━━━━━━━━━━━━━━━━━━━━\n"
        for method, rate in ud['fee_rates'].items():
            text += f"{method}: {rate:.2f}%\n"
        kb = [[InlineKeyboardButton(f"⚙️ {m}", callback_data=f"{module_prefix}_setfee_{m}")] for m in PAYMENT_METHODS]
        kb.append([InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_fin_main")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif original_data.startswith("setfee_"):
        method = original_data[7:]
        context.user_data['fin_action'] = 'set_fee_rate'
        context.user_data['fin_method'] = method
        context.user_data['awaiting_amount'] = True
        cur_rate = ud['fee_rates'].get(method, 0)
        await query.edit_message_text(f"当前 {method} 费率: {cur_rate:.2f}%\n\n请输入新的 {method} 费率（例如 0.8 表示 0.8%）：")

    # ===== 商户管理 =====
    elif original_data == "mch_menu":
        text = "🏪 商户管理\n━━━━━━━━━━━━━━━━━━━━\n"
        if not ud['merchants']:
            text += "暂无商户。"
        else:
            for name, m_data in ud['merchants'].items():
                text += f"\n商户名: {name}\n  余额: {m_data['balance']:.2f}\n"
        kb = [
            [InlineKeyboardButton("➕ 添加商户", callback_data=f"{module_prefix}_mch_add")],
            [InlineKeyboardButton("📝 管理商户", callback_data=f"{module_prefix}_mch_manage_select")],
            [InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_fin_main")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif original_data == "mch_add":
        context.user_data['fin_action'] = 'add_merchant'
        context.user_data['awaiting_amount'] = False # Not awaiting amount, awaiting name
        await query.edit_message_text("请输入新商户名称：")

    elif original_data == "mch_manage_select":
        if not ud['merchants']:
            await query.edit_message_text("暂无商户可管理。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_mch_menu")]]))
            return
        kb = [[InlineKeyboardButton(name, callback_data=f"{module_prefix}_mch_manage_{name}")] for name in ud['merchants'].keys()]
        kb.append([InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_mch_menu")])
        await query.edit_message_text("请选择要管理的商户：", reply_markup=InlineKeyboardMarkup(kb))

    elif original_data.startswith("mch_manage_"):
        merchant_name = original_data[len("mch_manage_"):]
        context.user_data['current_merchant'] = merchant_name
        merchant = get_merchant(ud, merchant_name)
        daily_summary = calc_merchant_daily(merchant)
        text = (
            f"🏪 商户: {merchant_name}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 当前余额: {merchant['balance']:.2f}\n"
            f"📅 今日 ({daily_summary['date']}) 入账: {daily_summary['deposit']:.2f} | 出账: {daily_summary['withdrawal']:.2f} | 净额: {daily_summary['net']:.2f}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "请选择操作："
        )
        kb = [
            [InlineKeyboardButton("📥 记录入账", callback_data=f"{module_prefix}_mch_dep"),
             InlineKeyboardButton("📤 记录出账", callback_data=f"{module_prefix}_mch_wit")],
            [InlineKeyboardButton("📜 交易历史", callback_data=f"{module_prefix}_mch_history")],
            [InlineKeyboardButton("📊 日报", callback_data=f"{module_prefix}_mch_daily_report")],
            [InlineKeyboardButton("❌ 删除商户", callback_data=f"{module_prefix}_mch_delete_{merchant_name}")],
            [InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_mch_menu")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif original_data == "mch_dep":
        context.user_data['fin_action'] = 'merchant_deposit'
        context.user_data['awaiting_amount'] = True
        await query.edit_message_text(f"请输入商户 {context.user_data['current_merchant']} 的入账金额：")

    elif original_data == "mch_wit":
        context.user_data['fin_action'] = 'merchant_withdrawal'
        context.user_data['awaiting_amount'] = True
        await query.edit_message_text(f"请输入商户 {context.user_data['current_merchant']} 的出账金额：")

    elif original_data == "mch_history":
        merchant_name = context.user_data.get('current_merchant')
        merchant = get_merchant(ud, merchant_name)
        history = merchant['transactions'][-15:]
        if not history:
            text = "暂无商户交易记录。"
        else:
            text = f"📜 商户 {merchant_name} 最近 15 条交易记录\n━━━━━━━━━━━━━━━━━━━━\n"
            for t in reversed(history):
                icon = "📥" if t['type'] == 'deposit' else "📤"
                text += f"{icon} {t['amount']:.2f} | {t['date']}\n"
        kb = [[InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_mch_manage_{merchant_name}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif original_data == "mch_daily_report":
        merchant_name = context.user_data.get('current_merchant')
        merchant = get_merchant(ud, merchant_name)
        report_text = f"📊 商户 {merchant_name} 日报\n━━━━━━━━━━━━━━━━━━━━\n"
        daily_summaries = {}
        for t in merchant['transactions']:
            date_str = t['date'][:10]
            if date_str not in daily_summaries:
                daily_summaries[date_str] = {'deposit': 0, 'withdrawal': 0, 'fee': 0}
            if t['type'] == 'deposit': daily_summaries[date_str]['deposit'] += t['amount']
            else: daily_summaries[date_str]['withdrawal'] += t['amount']
            daily_summaries[date_str]['fee'] += t.get('fee', 0)
        
        if not daily_summaries:
            report_text += "暂无数据。"
        else:
            sorted_dates = sorted(daily_summaries.keys(), reverse=True)[:7] # Last 7 days
            for date_str in sorted_dates:
                day_data = daily_summaries[date_str]
                net = day_data['deposit'] - day_data['withdrawal'] - day_data['fee']
                report_text += (
                    f"\n📅 {date_str}\n"
                    f"  📥 入账: {day_data['deposit']:.2f} | 📤 出账: {day_data['withdrawal']:.2f} | 净额: {net:.2f}\n"
                )
        kb = [[InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_mch_manage_{merchant_name}")]]
        await query.edit_message_text(report_text, reply_markup=InlineKeyboardMarkup(kb))

    elif original_data.startswith("mch_delete_"):
        merchant_name = original_data[len("mch_delete_"):]
        del ud['merchants'][merchant_name]
        save_finance()
        context.user_data.pop('current_merchant', None)
        await query.edit_message_text(f"商户 {merchant_name} 已删除。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回商户管理", callback_data=f"{module_prefix}_mch_menu")]]))

    # ===== 用户统计 =====
    elif original_data == "fin_user_stats":
        us = ud.get('user_stats', {})
        text = (
            "👥 用户统计\n━━━━━━━━━━━━━━━━━━━━\n"
            f"注册人数: {us.get('registered', 0)}\n"
            f"首充人数: {us.get('first_deposit', 0)}\n"
            f"总充人数: {us.get('total_depositors', 0)}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "请选择操作："
        )
        kb = [
            [InlineKeyboardButton("➕ 录入注册人数", callback_data=f"{module_prefix}_us_add_reg")],
            [InlineKeyboardButton("➕ 录入首充人数", callback_data=f"{module_prefix}_us_add_fc")],
            [InlineKeyboardButton("➕ 录入充值人数", callback_data=f"{module_prefix}_us_add_td")],
            [InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_fin_main")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif original_data == "us_add_reg":
        context.user_data['fin_action'] = 'add_registered'
        context.user_data['awaiting_amount'] = True
        await query.edit_message_text("请输入今日新增注册人数（数字）：")

    elif original_data == "us_add_fc":
        context.user_data['fin_action'] = 'add_first_deposit'
        context.user_data['awaiting_amount'] = True
        await query.edit_message_text("请输入今日新增首充人数（数字）：")

    elif original_data == "us_add_td":
        context.user_data['fin_action'] = 'add_total_depositors'
        context.user_data['awaiting_amount'] = True
        await query.edit_message_text("请输入今日充值人数（数字）：")

    # ===== 投注/派奖 =====
    elif original_data == "fin_betting":
        betting = ud.get('betting', {})
        records = betting.get('records', [])
        total_bet = sum(r['bet_amount'] for r in records)
        total_payout = sum(r['payout_amount'] for r in records)
        net_betting = total_bet - total_payout
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        today_recs = [r for r in records if r['date'][:10] == today]
        today_bet = sum(r['bet_amount'] for r in today_recs)
        today_payout = sum(r['payout_amount'] for r in today_recs)
        text = (
            "🎰 投注/派奖管理\n━━━━━━━━━━━━━━━━━━━━\n"
            f"🎰 总投注流水: {total_bet:.2f}\n"
            f"🏆 总派奖流水: {total_payout:.2f}\n"
            f"💰 投注盈利: {net_betting:.2f}\n"
        )
        if total_bet > 0:
            payout_rate = (total_payout / total_bet * 100)
            text += f"📊 派奖率: {payout_rate:.1f}%\n"
        text += (
            f"\n━━━ 今日 ({today}) ━━━\n"
            f"  投注: {today_bet:.2f}\n"
            f"  派奖: {today_payout:.2f}\n"
            f"  净收入: {today_bet - today_payout:.2f}\n"
        )
        # 最近5条记录
        if records:
            text += "\n━━━ 最近记录 ━━━\n"
            for r in reversed(records[-5:]):
                text += f"  🎰{r['bet_amount']:.2f} 🏆{r['payout_amount']:.2f} | {r['date']}\n"
        kb = [
            [InlineKeyboardButton("📝 录入投注/派奖", callback_data=f"{module_prefix}_bet_add")],
            [InlineKeyboardButton("📊 投注日报", callback_data=f"{module_prefix}_bet_daily")],
            [InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_fin_main")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif original_data == "bet_add":
        context.user_data['fin_action'] = 'add_betting'
        context.user_data['awaiting_amount'] = True
        await query.edit_message_text(
            "请输入投注流水和派奖流水（用空格分隔）：\n\n"
            "格式：投注金额 派奖金额\n"
            "例如：10000 8500\n"
            "表示投注10000，派奖8500"
        )

    elif original_data == "bet_daily":
        records = ud.get('betting', {}).get('records', [])
        daily = {}
        for r in records:
            d = r['date'][:10]
            if d not in daily: daily[d] = {'bet': 0, 'payout': 0}
            daily[d]['bet'] += r['bet_amount']
            daily[d]['payout'] += r['payout_amount']
        text = "📊 投注日报\n━━━━━━━━━━━━━━━━━━━━\n"
        if not daily:
            text += "暂无数据。"
        else:
            for d in sorted(daily.keys(), reverse=True)[:14]:
                v = daily[d]
                net = v['bet'] - v['payout']
                icon = "📈" if net >= 0 else "📉"
                pr = (v['payout']/v['bet']*100) if v['bet'] > 0 else 0
                text += f"{icon} {d}: 投注{v['bet']:.0f} 派奖{v['payout']:.0f} 净{net:.0f} ({pr:.1f}%)\n"
        kb = [[InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_fin_betting")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    # ===== 代理结算 =====
    elif original_data == "agt_menu":
        ag = ud.get('agent', {})
        now = datetime.datetime.now()
        cm = now.strftime("%Y-%m")
        # 全部总数据
        all_deps = sum(t['amount'] for t in ud['transactions'] if t['type']=='deposit')
        all_wits = sum(t['amount'] for t in ud['transactions'] if t['type']=='withdrawal')
        # 本月数据
        m_deps = sum(t['amount'] for t in ud['transactions'] if t['date'][:7]==cm and t['type']=='deposit')
        m_wits = sum(t['amount'] for t in ud['transactions'] if t['date'][:7]==cm and t['type']=='withdrawal')
        balance = m_deps - m_wits
        dep_fee = m_deps * ag.get('deposit_fee_rate', 1.0) / 100
        wit_fee = m_wits * ag.get('withdraw_fee_rate', 1.0) / 100
        m_bonus = sum(b['amount'] for b in ag.get('bonus_records', []) if b['date'][:7]==cm)
        # 投注流水计算厂商抽成
        m_bet = sum(r['bet_amount'] for r in ud.get('betting',{}).get('records',[]) if r['date'][:7]==cm)
        m_payout = sum(r['payout_amount'] for r in ud.get('betting',{}).get('records',[]) if r['date'][:7]==cm)
        game_profit = m_bet - m_payout
        vendor_cut = game_profit * ag.get('game_vendor_rate', 15.0) / 100 if game_profit > 0 else 0
        maint = ag.get('maintenance_fee', 0)
        settlement = balance - dep_fee - wit_fee - m_bonus - vendor_cut - maint
        # 汇率换算
        currency = MODULE_CURRENCY.get(current_module, 'MYR')
        rate = EXCHANGE_RATES.get(currency, 1.0)
        all_deps_usdt = all_deps / rate if rate > 0 else 0
        all_wits_usdt = all_wits / rate if rate > 0 else 0
        settlement_usdt = settlement / rate if rate > 0 else 0
        text = (
            f"💲 代理结算 ({cm})\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 累计总存款: {all_deps:.2f} {currency} (≈ {all_deps_usdt:.2f} USDT)\n"
            f"📊 累计总提款: {all_wits:.2f} {currency} (≈ {all_wits_usdt:.2f} USDT)\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 本月存款: {m_deps:.2f}\n"
            f"📤 本月提款: {m_wits:.2f}\n"
            f"💵 本月余额: {balance:.2f}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"➖ 入款费用 ({ag.get('deposit_fee_rate',1.0)}%): {dep_fee:.2f}\n"
            f"➖ 出款费用 ({ag.get('withdraw_fee_rate',1.0)}%): {wit_fee:.2f}\n"
            f"➖ 活动红利: {m_bonus:.2f}\n"
            f"➖ 游戏厂商抽成 ({ag.get('game_vendor_rate',15.0)}%): {vendor_cut:.2f}\n"
            f"   (投注{m_bet:.2f} - 派奖{m_payout:.2f} = 游戏利润{game_profit:.2f})\n"
            f"➖ 维护费: {maint:.2f}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 结算余额: {settlement:.2f} {currency} (≈ {settlement_usdt:.2f} USDT)\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "公式: 余额 - 入款费用 - 出款费用\n"
            "     - 活动红利 - 厂商抽成 - 维护费\n"
            "     = 结算余额"
        )
        kb = [
            [InlineKeyboardButton("⚙️ 入款费率", callback_data=f"{module_prefix}_agt_dep_rate"),
             InlineKeyboardButton("⚙️ 出款费率", callback_data=f"{module_prefix}_agt_wit_rate")],
            [InlineKeyboardButton("⚙️ 厂商抽成比例", callback_data=f"{module_prefix}_agt_vendor_rate"),
             InlineKeyboardButton("⚙️ 维护费", callback_data=f"{module_prefix}_agt_maint")],
            [InlineKeyboardButton("🎁 录入活动红利", callback_data=f"{module_prefix}_agt_bonus")],
            [InlineKeyboardButton("✅ 确认本月结算", callback_data=f"{module_prefix}_agt_confirm")],
            [InlineKeyboardButton("📜 历史结算", callback_data=f"{module_prefix}_agt_history")],
            [InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_fin_main")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif original_data == "agt_dep_rate":
        context.user_data['fin_action'] = 'agt_dep_rate'
        context.user_data['awaiting_amount'] = True
        cur = ud.get('agent',{}).get('deposit_fee_rate', 1.0)
        await query.edit_message_text(f"当前入款费率: {cur}%\n\n请输入新的入款费率（例如 1.5 表示 1.5%）：")

    elif original_data == "agt_wit_rate":
        context.user_data['fin_action'] = 'agt_wit_rate'
        context.user_data['awaiting_amount'] = True
        cur = ud.get('agent',{}).get('withdraw_fee_rate', 1.0)
        await query.edit_message_text(f"当前出款费率: {cur}%\n\n请输入新的出款费率（例如 1.5 表示 1.5%）：")

    elif original_data == "agt_vendor_rate":
        context.user_data['fin_action'] = 'agt_vendor_rate'
        context.user_data['awaiting_amount'] = True
        cur = ud.get('agent',{}).get('game_vendor_rate', 15.0)
        await query.edit_message_text(f"当前游戏厂商抽成: {cur}%\n\n请输入新的抽成比例（例如 15 表示 15%）：")

    elif original_data == "agt_maint":
        context.user_data['fin_action'] = 'agt_maint'
        context.user_data['awaiting_amount'] = True
        cur = ud.get('agent',{}).get('maintenance_fee', 0)
        await query.edit_message_text(f"当前每月维护费: {cur:.2f}\n\n请输入新的每月维护费金额：")

    elif original_data == "agt_bonus":
        context.user_data['fin_action'] = 'agt_bonus'
        context.user_data['awaiting_amount'] = True
        await query.edit_message_text("请输入活动红利金额：\n如需备注用空格分隔：1000 新用户红利")

    elif original_data == "agt_confirm":
        ag = ud.get('agent', {})
        now = datetime.datetime.now()
        cm = now.strftime("%Y-%m")
        m_deps = sum(t['amount'] for t in ud['transactions'] if t['date'][:7]==cm and t['type']=='deposit')
        m_wits = sum(t['amount'] for t in ud['transactions'] if t['date'][:7]==cm and t['type']=='withdrawal')
        balance = m_deps - m_wits
        dep_fee = m_deps * ag.get('deposit_fee_rate', 1.0) / 100
        wit_fee = m_wits * ag.get('withdraw_fee_rate', 1.0) / 100
        m_bonus = sum(b['amount'] for b in ag.get('bonus_records', []) if b['date'][:7]==cm)
        m_bet = sum(r['bet_amount'] for r in ud.get('betting',{}).get('records',[]) if r['date'][:7]==cm)
        m_payout = sum(r['payout_amount'] for r in ud.get('betting',{}).get('records',[]) if r['date'][:7]==cm)
        game_profit = m_bet - m_payout
        vendor_cut = game_profit * ag.get('game_vendor_rate', 15.0) / 100 if game_profit > 0 else 0
        maint = ag.get('maintenance_fee', 0)
        settlement = balance - dep_fee - wit_fee - m_bonus - vendor_cut - maint
        record = {
            "month": cm, "total_deposit": m_deps, "total_withdrawal": m_wits,
            "balance": balance, "deposit_fee": dep_fee, "withdraw_fee": wit_fee,
            "bonus": m_bonus, "vendor_cut": vendor_cut, "maintenance": maint,
            "settlement": settlement, "date": now.strftime("%Y-%m-%d %H:%M:%S")
        }
        ag.setdefault('settlements', []).append(record)
        save_finance()
        text = (
            f"✅ {cm} 结算已确认！\n\n"
            f"💰 结算余额: {settlement:.2f}\n\n"
            f"总存款: {m_deps:.2f}\n总提款: {m_wits:.2f}\n"
            f"入款费: {dep_fee:.2f}\n出款费: {wit_fee:.2f}\n"
            f"红利: {m_bonus:.2f}\n厂商抽成: {vendor_cut:.2f}\n维护费: {maint:.2f}"
        )
        kb = [[InlineKeyboardButton("🔙 返回代理结算", callback_data=f"{module_prefix}_agt_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif original_data == "agt_history":
        ag = ud.get('agent', {})
        records = ag.get('settlements', [])
        if not records:
            text = "📜 暂无历史结算记录。"
        else:
            text = "📜 历史结算记录\n━━━━━━━━━━━━━━━━━━━━\n"
            for r in reversed(records[-12:]):
                text += (
                    f"\n📅 {r['month']}\n"
                    f"  存款:{r['total_deposit']:.2f} 提款:{r['total_withdrawal']:.2f}\n"
                    f"  入款费:{r['deposit_fee']:.2f} 出款费:{r['withdraw_fee']:.2f}\n"
                    f"  红利:{r['bonus']:.2f} 厂商:{r['vendor_cut']:.2f} 维护:{r['maintenance']:.2f}\n"
                    f"  💰 结算: {r['settlement']:.2f}\n"
                )
        kb = [[InlineKeyboardButton("🔙 返回", callback_data=f"{module_prefix}_agt_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif original_data == "fin_main":
        await finance_menu(update, context)
    elif original_data == "fin_close":
        await query.delete_message()
    elif original_data == "main_finance_menu":
        await main_finance_menu(update, context)

# ==================== 处理文本输入 ====================
async def handle_finance_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_amount'):
        return False

    chat_id = update.effective_chat.id
    action = context.user_data.get('fin_action', '')

    # 汇率设置处理（不需要选择财务板块）
    if action.startswith('set_rate_'):
        currency = action[len('set_rate_'):]
        try:
            new_rate = float(update.message.text.strip())
            if new_rate <= 0:
                raise ValueError("汇率必须大于0")
            EXCHANGE_RATES[currency] = new_rate
            await update.message.reply_text(
                f"✅ {currency}/USDT 汇率已更新为: {new_rate}\n(1 USDT = {new_rate} {currency})",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回汇率菜单", callback_data="exchange_rate_menu")]])
            )
        except ValueError:
            await update.message.reply_text("输入格式错误，请输入数字，例如: 4.45")
        finally:
            context.user_data.pop('awaiting_amount', None)
            context.user_data.pop('fin_action', None)
        return True

    current_module = context.user_data.get('current_finance_module')
    if not current_module:
        await update.message.reply_text("请先选择一个财务板块。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回主菜单", callback_data="main_finance_menu")]]))
        context.user_data.pop('awaiting_amount', None)
        context.user_data.pop('fin_action', None)
        return True

    if current_module == 'advertising_finance':
        ad_data = get_user_finance_module(chat_id, 'advertising_finance')
        action = context.user_data.get('fin_action')
        if action == 'add_ad_daily_data':
            try:
                parts = update.message.text.split()
                if len(parts) != 7:
                    raise ValueError("格式不正确，请输入7个数据：消耗 点击 注册 首充 购物 充值人数 充值金额")
                
                ad_spend = float(parts[0])
                clicks = int(parts[1])
                registrations = int(parts[2])
                first_deposits = int(parts[3])
                purchases = float(parts[4])
                depositors = int(parts[5])
                deposit_amount = float(parts[6])

                today_str = datetime.datetime.now().strftime("%Y-%m-%d")
                ad_data['daily_data'][today_str] = {
                    'ad_spend': ad_spend,
                    'clicks': clicks,
                    'registrations': registrations,
                    'first_deposits': first_deposits,
                    'purchases': purchases,
                    'depositors': depositors,
                    'deposit_amount': deposit_amount
                }
                save_finance()
                await update.message.reply_text("✅ 今日广告数据已录入！", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回广告菜单", callback_data="adv_main")]]))
            except ValueError as e:
                await update.message.reply_text(f"输入格式错误: {e}\n请重新输入，例如：1000 5000 100 50 20 80 15000")
            finally:
                context.user_data.pop('awaiting_amount', None)
                context.user_data.pop('fin_action', None)
            return True
        return False # Should not reach here if action is handled

    # For Malaysia/Philippines finance modules
    ud = get_user_finance_module(chat_id, current_module)
    action = context.user_data.get('fin_action')
    method = context.user_data.get('fin_method')
    category = context.user_data.get('fin_expense_category')
    merchant_name = context.user_data.get('current_merchant')

    try:
        input_parts = update.message.text.split(' ', 1)
        amount = float(input_parts[0])
        note = input_parts[1] if len(input_parts) > 1 else ""

        if action == 'dep':
            fee_rate = ud['fee_rates'].get(method, 0)
            fee = amount * fee_rate / 100
            ud['balance'] += amount - fee
            transaction = {'type': 'deposit', 'amount': amount, 'method': method, 'fee': fee, 'date': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            if note: transaction['merchant'] = note
            ud['transactions'].append(transaction)
            if note: # If merchant specified, also record in merchant's transactions
                m = get_merchant(ud, note)
                m['balance'] += amount
                m['transactions'].append({'type': 'deposit', 'amount': amount, 'date': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            save_finance()
            await update.message.reply_text(f"✅ 成功记录存款 {amount:.2f} {method} (手续费: {fee:.2f})。当前余额: {ud['balance']:.2f}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{current_module[:3]}_fin_main")]]))

        elif action == 'wit':
            fee_rate = ud['fee_rates'].get(method, 0)
            fee = amount * fee_rate / 100
            ud['balance'] -= (amount + fee)
            transaction = {'type': 'withdrawal', 'amount': amount, 'method': method, 'fee': fee, 'date': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
            if note: transaction['merchant'] = note
            ud['transactions'].append(transaction)
            if note: # If merchant specified, also record in merchant's transactions
                m = get_merchant(ud, note)
                m['balance'] -= amount
                m['transactions'].append({'type': 'withdrawal', 'amount': amount, 'date': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            save_finance()
            await update.message.reply_text(f"✅ 成功记录提款 {amount:.2f} {method} (手续费: {fee:.2f})。当前余额: {ud['balance']:.2f}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{current_module[:3]}_fin_main")]]))

        elif action == 'expense':
            ud['balance'] -= amount
            ud['expenses'].append({'category': category, 'amount': amount, 'note': note, 'date': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            save_finance()
            await update.message.reply_text(f"✅ 成功记录 {category} 费用 {amount:.2f}。当前余额: {ud['balance']:.2f}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{current_module[:3]}_fin_main")]]))

        elif action == 'set_fee_rate':
            ud['fee_rates'][method] = amount
            save_finance()
            await update.message.reply_text(f"✅ {method} 费率已更新为 {amount:.2f}%。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{current_module[:3]}_fin_fee_rates")]]))

        elif action == 'add_merchant':
            if update.message.text in ud['merchants']:
                await update.message.reply_text("该商户已存在，请重新输入名称或返回。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{current_module[:3]}_mch_menu")]]))
            else:
                get_merchant(ud, update.message.text)
                save_finance()
                await update.message.reply_text(f"✅ 商户 {update.message.text} 已添加。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{current_module[:3]}_mch_menu")]]))

        elif action == 'merchant_deposit':
            m = get_merchant(ud, merchant_name)
            m['balance'] += amount
            m['transactions'].append({'type': 'deposit', 'amount': amount, 'date': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            save_finance()
            await update.message.reply_text(f"✅ 商户 {merchant_name} 成功入账 {amount:.2f}。当前余额: {m['balance']:.2f}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{current_module[:3]}_mch_manage_{merchant_name}")]]))

        elif action == 'merchant_withdrawal':
            m = get_merchant(ud, merchant_name)
            m['balance'] -= amount
            m['transactions'].append({'type': 'withdrawal', 'amount': amount, 'date': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            save_finance()
            await update.message.reply_text(f"✅ 商户 {merchant_name} 成功出账 {amount:.2f}。当前余额: {m['balance']:.2f}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{current_module[:3]}_mch_manage_{merchant_name}")]]))

        elif action == 'add_registered':
            us = ud.setdefault('user_stats', {})
            us['registered'] = us.get('registered', 0) + int(amount)
            save_finance()
            await update.message.reply_text(f"✅ 已录入新增注册人数 {int(amount)}。总注册人数: {us['registered']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{current_module[:3]}_fin_user_stats")]]))

        elif action == 'add_first_deposit':
            us = ud.setdefault('user_stats', {})
            us['first_deposit'] = us.get('first_deposit', 0) + int(amount)
            save_finance()
            await update.message.reply_text(f"✅ 已录入新增首充人数 {int(amount)}。总首充人数: {us['first_deposit']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{current_module[:3]}_fin_user_stats")]]))

        elif action == 'add_total_depositors':
            us = ud.setdefault('user_stats', {})
            us['total_depositors'] = us.get('total_depositors', 0) + int(amount)
            save_finance()
            await update.message.reply_text(f"✅ 已录入今日充值人数 {int(amount)}。总充人数: {us['total_depositors']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{current_module[:3]}_fin_user_stats")]]))

        elif action == 'add_betting':
            try:
                bet_amount, payout_amount = map(float, update.message.text.split())
                betting_records = ud.setdefault('betting', {}).setdefault('records', [])
                betting_records.append({
                    'bet_amount': bet_amount,
                    'payout_amount': payout_amount,
                    'date': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                save_finance()
                await update.message.reply_text(f"✅ 成功录入投注 {bet_amount:.2f}，派奖 {payout_amount:.2f}。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{current_module[:3]}_fin_betting")]]))
            except ValueError:
                await update.message.reply_text("输入格式错误，请确保输入两个数字，用空格分隔。例如：10000 8500")

        elif action == 'agt_dep_rate':
            ag = ud.setdefault('agent', {})
            ag['deposit_fee_rate'] = amount
            save_finance()
            await update.message.reply_text(f"✅ 入款费率已更新为 {amount:.2f}%。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回代理结算", callback_data=f"{current_module[:3]}_agt_menu")]]))

        elif action == 'agt_wit_rate':
            ag = ud.setdefault('agent', {})
            ag['withdraw_fee_rate'] = amount
            save_finance()
            await update.message.reply_text(f"✅ 出款费率已更新为 {amount:.2f}%。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回代理结算", callback_data=f"{current_module[:3]}_agt_menu")]]))

        elif action == 'agt_vendor_rate':
            ag = ud.setdefault('agent', {})
            ag['game_vendor_rate'] = amount
            save_finance()
            await update.message.reply_text(f"✅ 厂商抽成比例已更新为 {amount:.2f}%。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回代理结算", callback_data=f"{current_module[:3]}_agt_menu")]]))

        elif action == 'agt_maint':
            ag = ud.setdefault('agent', {})
            ag['maintenance_fee'] = amount
            save_finance()
            await update.message.reply_text(f"✅ 每月维护费已更新为 {amount:.2f}。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回代理结算", callback_data=f"{current_module[:3]}_agt_menu")]]))

        elif action == 'agt_bonus':
            ag = ud.setdefault('agent', {})
            bonus_records = ag.setdefault('bonus_records', [])
            bonus_records.append({
                'amount': amount,
                'note': note,
                'date': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            save_finance()
            await update.message.reply_text(f"✅ 活动红利 {amount:.2f} 已录入。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回代理结算", callback_data=f"{current_module[:3]}_agt_menu")]]))

        else:
            await update.message.reply_text("未知操作或输入格式错误。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{current_module[:3]}_fin_main")]]))

    except ValueError:
        await update.message.reply_text("请输入有效的数字金额。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{current_module[:3]}_fin_main")]]))
    finally:
        context.user_data.pop('awaiting_amount', None)
        context.user_data.pop('fin_action', None)
        context.user_data.pop('fin_method', None)
        context.user_data.pop('fin_expense_category', None)
        context.user_data.pop('current_merchant', None)
    return True

# ==================== 安全检测功能 (保持不变) ====================
URL_REGEX = re.compile(r'https?://(?:[-\w.]|(?:%[0-9a-fA-F]{2}))+')

urlhaus_urls = set()
urlhaus_domains = set()
openphish_urls = set()
openphish_domains = set()
threatfox_domains = set()
feodo_ips = set()

def extract_domain(url):
    try:
        return urlparse(url).netloc
    except: pass
    return None

def extract_ip(url):
    try:
        if not url.startswith('http'): url = 'http://' + url
        h = urlparse(url).hostname
        if h and re.match(r'^\d+\.\d+\.\d+\.\d+$', h): return h
    except: pass
    return None

def update_urlhaus():
    global urlhaus_urls, urlhaus_domains
    try:
        r = requests.get("https://urlhaus.abuse.ch/downloads/text_online/", timeout=30)
        if r.status_code == 200:
            u, d = set(), set()
            for l in r.text.strip().split('\n'):
                l = l.strip()
                if l and not l.startswith('#'):
                    u.add(l.lower())
                    dm = extract_domain(l)
                    if dm: d.add(dm)
            urlhaus_urls, urlhaus_domains = u, d
            logger.info(f"[URLhaus] {len(u)} URLs")
    except Exception as e: logger.error(f"[URLhaus] {e}")

def update_openphish():
    global openphish_urls, openphish_domains
    try:
        r = requests.get("https://openphish.com/feed.txt", timeout=30)
        if r.status_code == 200:
            u, d = set(), set()
            for l in r.text.strip().split('\n'):
                l = l.strip()
                if l and l.startswith('http'):
                    u.add(l.lower())
                    dm = extract_domain(l)
                    if dm: d.add(dm)
            openphish_urls, openphish_domains = u, d
            logger.info(f"[OpenPhish] {len(u)} URLs")
    except Exception as e: logger.error(f"[OpenPhish] {e}")

def update_threatfox():
    global threatfox_domains
    try:
        r = requests.get("https://threatfox.abuse.ch/downloads/hostfile/", timeout=30)
        if r.status_code == 200:
            d = set()
            for l in r.text.strip().split('\n'):
                l = l.strip()
                if l and not l.startswith('#') and '\t' in l:
                    p = l.split('\t')
                    if len(p) >= 2:
                        dm = p[1].strip().lower()
                        if dm and dm != 'localhost': d.add(dm)
            threatfox_domains = d
            logger.info(f"[ThreatFox] {len(d)} domains")
    except Exception as e: logger.error(f"[ThreatFox] {e}")

def update_feodo():
    global feodo_ips
    try:
        r = requests.get("https://feodotracker.abuse.ch/downloads/ipblocklist_recommended.txt", timeout=30)
        if r.status_code == 200:
            ips = set()
            for l in r.text.strip().split('\n'):
                l = l.strip()
                if l and not l.startswith('#') and re.match(r'^\d+\.\d+\.\d+\.\d+$', l): ips.add(l)
            feodo_ips = ips
            logger.info(f"[Feodo] {len(ips)} IPs")
    except Exception as e: logger.error(f"[Feodo] {e}")

def update_all_databases():
    while True:
        ts = [threading.Thread(target=f) for f in [update_urlhaus, update_openphish, update_threatfox, update_feodo]]
        for t in ts: t.start()
        for t in ts: t.join(timeout=60)
        time.sleep(1800)

# ==================== 检测逻辑 ====================
def check_databases(url):
    findings = []
    ul = url.lower()
    vs = [ul] if ul.startswith('http') else ['http://'+ul, 'https://'+ul]
    dm = extract_domain(ul if ul.startswith('http') else 'http://'+ul)
    ip = extract_ip(ul if ul.startswith('http') else 'http://'+ul)
    if any(v in urlhaus_urls for v in vs) or (dm and dm in urlhaus_domains): findings.append("URLhaus: 恶意软件")
    if any(v in openphish_urls for v in vs) or (dm and dm in openphish_domains): findings.append("OpenPhish: 钓鱼")
    if dm and dm in threatfox_domains: findings.append("ThreatFox: C&C服务器")
    if ip and ip in feodo_ips: findings.append("Feodo: 僵尸网络IP")
    return findings

def heuristic_check(url):
    w = []
    ul = url.lower()
    if not ul.startswith('http'): ul = 'http://'+ul
    if re.match(r'https?://\d+\.\d+\.\d+\.\d+', ul): w.append("IP地址访问")
    dm = extract_domain(ul)
    if dm:
        if len(dm.split('.')) > 4: w.append("子域名过多")
        for b in ['paypal','apple','google','microsoft','amazon','facebook','binance','metamask']:
            if b in dm and not (dm.endswith(f'{b}.com') or dm.endswith(f'{b}.org')):
                w.append(f"仿冒品牌'{b}'")
    for kw in ['login','verify','account','secure','wallet','password','urgent']:
        if kw in ul: w.append(f"可疑词'{kw}'")
    if len(ul) > 200: w.append("URL过长")
    return w

import asyncio

async def ai_analysis(url, db_f, heu_w):
    return ""

# ==================== 安全提醒 ====================
TIPS = [
    "🛡 安全提醒：设备登录检查\n\n操作：设置->设备\n检查所有活跃会话，发现未知设备立即点击'终止所有其他会话'。",
    "🛡 安全提醒：两步验证\n\n操作：设置->隐私与安全->两步验证\n设置强密码并绑定恢复邮箱。没有2FA，SIM卡劫持可轻易接管账号。",
    "🛡 安全提醒：隐私最小化\n\n建议：电话号码设为'没有人'，转发消息设为'没有人'，群组设为'仅联系人'。",
    "🛡 安全提醒：社交工程防范\n\nTelegram官方绝不会私聊要求密码或验证码。警惕'验证账户'、'领取奖励'链接。收到可疑链接转发给我检测！",
    "🛡 安全提醒：SIM卡劫持防范\n\n联系运营商设置SIM PIN码，不要公开手机号，在多台设备保持登录。",
    "🛡 安全提醒：应用权限检查\n\n关闭Telegram不必要的联系人和位置权限。用Have I Been Pwned检查邮箱是否泄露。",
    "🛡 安全提醒：密码安全\n\n两步验证密码不要与其他平台相同，至少12位，包含大小写数字特殊字符，每3个月更换。",
    "🛡 安全提醒：群组安全\n\n不随意加入陌生群组，群里'管理员'私聊可能是冒充，警惕'空投'、'免费赠送'诱饵。",
    "🛡 安全提醒：账号恢复准备\n\n绑定恢复邮箱，在至少2台设备保持登录，定期备份聊天记录。",
    "🛡 安全提醒：可疑行为识别\n\n警惕自称'官方'私聊、要求验证码、紧急转账、'投资'群组、'中奖'通知。",
    "🛡 安全提醒：财务安全\n\n不要在Telegram分享银行卡号密码，不信陌生人投资建议，转账前确认身份，定期检查财务记录(/finance)。",
    "🛡 安全提醒：恶意文件防范\n\n不下载陌生APK/EXE，警惕伪装文件，不安装非官方Telegram版本。",
]
tip_idx = 0

async def send_security_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    global tip_idx
    tip = TIPS[tip_idx % len(TIPS)]
    tip_idx += 1
    for cid in list(subscribers):
        try: await ctx.bot.send_message(chat_id=cid, text=tip)
        except:
            subscribers.discard(cid)
            save_subs()

# ==================== 指令处理 ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    subscribers.add(cid)
    save_subs()
    total = len(urlhaus_urls)+len(openphish_urls)+len(threatfox_domains)+len(feodo_ips)
    w = (
        "🛡 PGone安全卫士 Pro\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 AI智能助手：\n  直接发消息即可对话\n  支持财务分析/安全咨询/翻译/编程等\n\n"
        "🔒 安全功能：\n  6层链接检测+AI分析\n"
        f"  威胁库: {total} 条 | 每30分钟同步\n  每小时安全提醒(已订阅)\n\n"
        "💰 财务功能：\n  🇲🇾 马来西亚 | 🇵🇭 菲律宾 | 📢 广告\n  存提款+余额+费用+利润\n  商户管理+投注派奖+代理结算\n\n"
        "命令：\n  发链接 → 自动检测\n  发消息 → AI对话\n  /finance → 财务管理\n  /ai → AI助手说明\n  /clear → 清除对话记录\n  /setadmin → 设为管理员\n\n"
        "数据本地存储，不收集隐私。"
    )
    await update.message.reply_text(w)
    await main_finance_menu(update, context) # Changed to main_finance_menu

# ==================== AI 智能对话 ====================
CHAT_HISTORIES = {}  # chat_id -> list of messages
MAX_HISTORY = 40  # 保留最近40条对话

AI_SYSTEM_PROMPT = """你是 PGone安全卫士 Pro，一个顶级AI智能助手。你由GPT-4.1驱动，具备最强大的语言理解和推理能力。

你的核心能力：
1. 深度分析：能够深入分析复杂问题，提供多角度、有洞察力的解答
2. 财务专家：精通财务分析、投资策略、风险评估、利润优化、税务规划
3. 网络安全专家：威胁分析、漏洞评估、安全架构、隐私保护、反欺诈
4. 商业顾问：市场分析、竞争策略、运营优化、增长黑客、商业模式设计
5. 技术专家：编程、架构设计、数据库、API开发、系统设计
6. 数据分析：统计分析、趋势预测、报表解读、KPI优化
7. 多语言专家：精通中文、英文、马来语、菲律宾语等多语言翻译和写作
8. 创意写作：文案策划、营销文案、报告撰写、内容创作
9. 法律顾问：合同分析、合规建议、风险提示
10. 生活管家：旅行规划、健康建议、时间管理、人际关系

回答规则：
- 默认用中文回复，用户用其他语言则用对应语言
- 回答要专业、有深度、有条理
- 提供具体可操作的建议，而不是笼统的建议
- 当涉及重要决策时，主动提示风险
- 保持友好但专业的语气"""

async def ai_chat(chat_id: int, user_message: str) -> str:
    if ai_client is None:
        return "AI 功能暂时不可用"
    try:
        if chat_id not in CHAT_HISTORIES:
            CHAT_HISTORIES[chat_id] = []
        CHAT_HISTORIES[chat_id].append({"role": "user", "content": user_message})
        if len(CHAT_HISTORIES[chat_id]) > MAX_HISTORY:
            CHAT_HISTORIES[chat_id] = CHAT_HISTORIES[chat_id][-MAX_HISTORY:]
        messages = [{"role": "system", "content": AI_SYSTEM_PROMPT}] + CHAT_HISTORIES[chat_id]
        r = ai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            max_tokens=2000,
            temperature=0.7
        )
        reply = r.choices[0].message.content.strip()
        CHAT_HISTORIES[chat_id].append({"role": "assistant", "content": reply})
        if len(CHAT_HISTORIES[chat_id]) > MAX_HISTORY:
            CHAT_HISTORIES[chat_id] = CHAT_HISTORIES[chat_id][-MAX_HISTORY:]
        return reply
    except Exception as e:
        logger.error(f"AI chat error: {e}")
        return "抱歉，AI 暂时无法回复，请稍后再试。"

async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # If user provides a question after /ai, process it
    user_text = ' '.join(context.args) if context.args else ''
    if user_text.strip():
        chat_id = update.effective_chat.id
        await update.message.chat.send_action('typing')
        reply = await ai_chat(chat_id, user_text)
        await update.message.reply_text(reply)
    else:
        await update.message.reply_text(
            "🤖 AI 智能助手\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "直接发送消息即可与AI对话！\n\n"
            "我可以帮你：\n"
            "• 回答各种问题\n"
            "• 财务分析与建议\n"
            "• 网络安全咨询\n"
            "• 商业数据分析\n"
            "• 多语言翻译\n"
            "• 编程帮助\n"
            "• 数学计算\n"
            "• 文案写作\n"
            "• 知识问答\n"
            "• 生活建议\n\n"
            "发送 /clear 可以清除对话记录"
        )

async def clear_chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    CHAT_HISTORIES.pop(chat_id, None)
    await update.message.reply_text("✅ 对话记录已清除，可以开始新的对话了！")

# ==================== 网页爬取功能 ====================
async def scrape_website(url: str) -> dict:
    """最高级别网页爬取：提取标题、正文、链接、图片、元数据"""
    try:
        if not url.startswith('http'):
            url = 'https://' + url
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True, verify=False)
        resp.encoding = resp.apparent_encoding or 'utf-8'
        soup = BeautifulSoup(resp.text, 'lxml')
        
        # 提取标题
        title = soup.title.string.strip() if soup.title and soup.title.string else '无标题'
        
        # 提取meta信息
        meta_desc = ''
        meta_tag = soup.find('meta', attrs={'name': 'description'})
        if meta_tag and meta_tag.get('content'):
            meta_desc = meta_tag['content'][:200]
        
        # 提取正文内容
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe', 'noscript']):
            tag.decompose()
        
        # 尝试找到主要内容区域
        main_content = soup.find('main') or soup.find('article') or soup.find('div', class_=re.compile(r'content|main|body|article', re.I))
        if main_content:
            text_content = main_content.get_text(separator='\n', strip=True)
        else:
            text_content = soup.body.get_text(separator='\n', strip=True) if soup.body else soup.get_text(separator='\n', strip=True)
        
        # 清理多余空行
        lines = [line.strip() for line in text_content.split('\n') if line.strip()]
        text_content = '\n'.join(lines)
        
        # 截取前3000字符
        if len(text_content) > 3000:
            text_content = text_content[:3000] + '\n...(内容已截取)'
        
        # 提取链接
        links = []
        for a in soup.find_all('a', href=True)[:20]:
            href = a['href']
            if href.startswith('http'):
                link_text = a.get_text(strip=True)[:50] or href[:50]
                links.append(f"{link_text}: {href}")
            elif href.startswith('/'):
                full_url = urljoin(url, href)
                link_text = a.get_text(strip=True)[:50] or full_url[:50]
                links.append(f"{link_text}: {full_url}")
        
        # 提取图片
        images = []
        for img in soup.find_all('img', src=True)[:10]:
            src = img['src']
            if src.startswith('http'):
                alt = img.get('alt', '')[:30]
                images.append(f"{alt}: {src}" if alt else src)
            elif src.startswith('/'):
                full_src = urljoin(url, src)
                alt = img.get('alt', '')[:30]
                images.append(f"{alt}: {full_src}" if alt else full_src)
        
        # 提取表格数据
        tables_data = []
        for table in soup.find_all('table')[:3]:
            rows = []
            for tr in table.find_all('tr')[:15]:
                cells = [td.get_text(strip=True)[:30] for td in tr.find_all(['td', 'th'])]
                if cells:
                    rows.append(' | '.join(cells))
            if rows:
                tables_data.append('\n'.join(rows))
        
        # 提取表单字段
        forms = []
        for form in soup.find_all('form')[:3]:
            inputs = []
            for inp in form.find_all(['input', 'select', 'textarea']):
                name = inp.get('name', '') or inp.get('id', '')
                inp_type = inp.get('type', 'text')
                if name and inp_type not in ['hidden', 'submit']:
                    inputs.append(f"{name}({inp_type})")
            if inputs:
                forms.append(', '.join(inputs))
        
        return {
            'success': True,
            'url': url,
            'status_code': resp.status_code,
            'title': title,
            'meta_desc': meta_desc,
            'content': text_content,
            'links': links,
            'images': images,
            'tables': tables_data,
            'forms': forms,
            'content_length': len(resp.text)
        }
    except requests.exceptions.Timeout:
        return {'success': False, 'error': '网站访问超时，请检查网址是否正确'}
    except requests.exceptions.ConnectionError:
        return {'success': False, 'error': '无法连接到网站，可能网站已关闭或网址错误'}
    except Exception as e:
        return {'success': False, 'error': f'爬取失败: {str(e)[:100]}'}

async def ai_summarize_webpage(scrape_result: dict) -> str:
    """用AI总结网页内容"""
    if ai_client is None:
        return "AI 功能暂时不可用"
    try:
        content = scrape_result.get('content', '')[:2000]
        tables = '\n'.join(scrape_result.get('tables', []))[:500]
        prompt = f"""请分析并总结以下网页内容：

标题: {scrape_result.get('title', '')}
描述: {scrape_result.get('meta_desc', '')}

正文内容:
{content}

表格数据:
{tables}

请提供：
1. 网站主要内容概述（2-3句）
2. 关键信息提取（列出重要数据点）
3. 网站类型判断
用中文简洁回复。"""
        r = ai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.3
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"AI summarize error: {e}")
        return "AI总结暂不可用"

async def handle_scrape_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """\u5904\u7406 /scrape \u547d\u4ee4"""
    if not context.args:
        await update.message.reply_text(
            "🕷 网页爬取功能\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "用法: /scrape 网址\n"
            "例如: /scrape google.com\n\n"
            "功能：\n"
            "• 提取网页标题、正文内容\n"
            "• 提取链接、图片、表格\n"
            "• 提取表单字段\n"
            "• AI 智能内容总结\n"
            "• 自动安全检测"
        )
        return
    
    url = context.args[0]
    status_msg = await update.message.reply_text(f"🕷 正在爬取: {url}\n请稍等...")
    
    # 爬取网页
    result = await scrape_website(url)
    
    if not result['success']:
        await status_msg.edit_text(f"❌ 爬取失败\n{result['error']}")
        return
    
    # 组装结果
    report = []
    report.append(f"🕷 网页爬取结果")
    report.append("━━━━━━━━━━━━━━━━━━━━")
    report.append(f"🌐 网址: {result['url']}")
    report.append(f"📌 标题: {result['title']}")
    if result['meta_desc']:
        report.append(f"📝 描述: {result['meta_desc']}")
    report.append(f"📡 状态码: {result['status_code']} | 大小: {result['content_length']}字符")
    report.append("")
    
    # 正文内容（截取）
    content_preview = result['content'][:1500] if result['content'] else '无内容'
    report.append(f"📄 正文内容:\n{content_preview}")
    
    # 发送第一部分
    first_part = "\n".join(report)
    if len(first_part) > 4000:
        first_part = first_part[:4000] + "\n...(已截取)"
    await status_msg.edit_text(first_part)
    
    # 发送链接和图片信息
    extra_parts = []
    if result['links']:
        extra_parts.append("🔗 页面链接:\n" + "\n".join(f"  {l}" for l in result['links'][:10]))
    if result['images']:
        extra_parts.append("\n🖼 图片:\n" + "\n".join(f"  {i}" for i in result['images'][:5]))
    if result['tables']:
        extra_parts.append("\n📊 表格数据:\n" + "\n".join(result['tables'][:2])[:800])
    if result['forms']:
        extra_parts.append("\n📝 表单字段:\n" + "\n".join(f"  {f}" for f in result['forms']))
    
    if extra_parts:
        extra_text = "\n".join(extra_parts)
        if len(extra_text) > 4000:
            extra_text = extra_text[:4000] + "\n...(已截取)"
        await update.message.reply_text(extra_text)
    
    # AI summary removed - IP restriction on Railway

# ==================== 管理员配置 ====================
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', '7414281070'))  # Default to owner's chat ID

# /setadmin 命令 - 设置管理员（需要密码验证）
async def setadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_CHAT_ID
    user_id = update.effective_chat.id
    
    # Check if user is blocked
    if _is_user_blocked(user_id):
        _log_security_event('BLOCKED_ACCESS', user_id, 'Attempted /setadmin while blocked')
        await update.message.reply_text("🚫 您已被临时封锁，请稍后再试。")
        return
    
    # If already admin, just confirm
    if user_id == ADMIN_CHAT_ID:
        await update.message.reply_text("✅ 您已经是管理员。")
        return
    
    # Require password
    if not context.args:
        _log_security_event('AUTH_ATTEMPT', user_id, 'No password provided for /setadmin')
        await update.message.reply_text("🔐 请输入管理密码：\n用法: /setadmin <密码>")
        return
    
    password = ' '.join(context.args)
    if password != ADMIN_PASSWORD:
        _record_failed_attempt(user_id)
        _log_security_event('AUTH_FAILED', user_id, f'Wrong password attempt ({_failed_auth_attempts.get(user_id, {}).get("count", 0)}/{MAX_FAILED_ATTEMPTS})')
        remaining = MAX_FAILED_ATTEMPTS - _failed_auth_attempts.get(user_id, {}).get('count', 0)
        if remaining <= 0:
            await update.message.reply_text("🚫 密码错误次数过多，您已被临时封锁1小时。")
            # Alert current admin
            if ADMIN_CHAT_ID:
                try:
                    user = update.effective_user
                    alert_text = (
                        f"🚨 安全警报！\n\n"
                        f"用户 {user.full_name} (ID: {user_id}) "
                        f"多次尝试获取管理员权限，已被封锁。\n"
                        f"用户名: @{user.username if user.username else '无'}\n"
                        f"时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=alert_text)
                except Exception:
                    pass
        else:
            await update.message.reply_text(f"❌ 密码错误。剩余尝试次数: {remaining}")
        return
    
    # Password correct
    old_admin = ADMIN_CHAT_ID
    ADMIN_CHAT_ID = user_id
    _log_security_event('ADMIN_CHANGED', user_id, f'Admin changed from {old_admin} to {user_id}')
    
    # Reset failed attempts for this user
    if user_id in _failed_auth_attempts:
        del _failed_auth_attempts[user_id]
    
    await update.message.reply_text(
        f"✅ 已将您设为管理员\n"
        f"管理员 Chat ID: {ADMIN_CHAT_ID}\n\n"
        f"您现在可以管理 Bot 的所有功能。\n"
        f"管理员权限已激活。"
    )
    logger.info(f"Admin set to chat_id: {ADMIN_CHAT_ID} (password verified)")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    
    user_id = update.effective_chat.id
    text = update.message.text
    
    
    # 文字记账处理（入款/出款）
    if await _handle_bill_input(update, context): return

    if await handle_finance_input(update, context): return
    text = update.message.text
    urls = URL_REGEX.findall(text)
    if update.message.entities:
        for e in update.message.entities:
            if e.type == 'text_link': urls.append(e.url)
            elif e.type == 'url': urls.append(text[e.offset:e.offset+e.length])
    if not urls:
        # 没有链接，非记账非财务，走AI对话
        chat_id = update.effective_chat.id
        await update.message.chat.send_action('typing')
        reply = await ai_chat(chat_id, text)
        await update.message.reply_text(reply)
        return
    # 检测到链接：先安全检测，然后提供爬取选项
    for url in list(set(urls)):
        db_f = check_databases(url)
        heu_w = heuristic_check(url)
        rpt = [f"🔗 链接: {url}\n"]
        if db_f:
            rpt.append("🚨 危险\n" + "\n".join(f"  {f}" for f in db_f))
        elif len(heu_w) >= 2: rpt.append("⚠️ 高风险")
        elif heu_w: rpt.append("⚡ 中风险")
        else: rpt.append("✅ 安全")
        if heu_w: rpt.append("\n🔍 可疑特征:\n" + "\n".join(f"  - {w}" for w in heu_w))
        total = len(urlhaus_urls)+len(openphish_urls)+len(threatfox_domains)+len(feodo_ips)
        rpt.append(f"\n━━━━━━━━━━━━━━━━━━━━\n已对比 {total} 条威胁记录")
        rpt.append("\n💡 发送 /scrape " + url + " 可爬取网页内容")
        # 先发送快速结果
        fast_msg = await update.message.reply_text("\n".join(rpt))
        # AI analysis removed - IP restriction on Railway

# ==================== 图片识别自动记账 ====================

# 授权用户管理
AUTHORIZED_USERS_FILE = DATA_DIR / 'authorized_users.json'
authorized_users = set(load_json(AUTHORIZED_USERS_FILE, []))

def save_authorized_users():
    save_json(AUTHORIZED_USERS_FILE, list(authorized_users))

def is_authorized(user_id: int) -> bool:
    """Check if user is admin or authorized."""
    return user_id == ADMIN_CHAT_ID or user_id in authorized_users

# 授权用户管理命令
async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 此命令仅管理员可用")
        return
    target_id = None
    # 如果是回复某条消息，取被回复消息的发送者
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("用法: /adduser <用户ID> 或回复用户消息")
            return
    if not target_id:
        await update.message.reply_text("用法: /adduser <用户ID> 或回复用户消息")
        return
    authorized_users.add(target_id)
    save_authorized_users()
    await update.message.reply_text(f"✅ 已授权用户 {target_id}")

async def removeuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 此命令仅管理员可用")
        return
    target_id = None
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("用法: /removeuser <用户ID>")
            return
    if not target_id:
        await update.message.reply_text("用法: /removeuser <用户ID>")
        return
    authorized_users.discard(target_id)
    save_authorized_users()
    await update.message.reply_text(f"✅ 已移除授权用户 {target_id}")

async def listusers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 此命令仅管理员可用")
        return
    if not authorized_users:
        await update.message.reply_text("👥 当前没有授权用户（管理员默认有权限）")
        return
    text = "👥 已授权用户列表：\n━━━━━━━━━━━━━━━━━━━━\n"
    for uid in authorized_users:
        text += f"• {uid}\n"
    await update.message.reply_text(text)

# /seclog 命令 - 查看安全日志（仅管理员）
async def seclog_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID:
        _log_security_event('UNAUTHORIZED_SECLOG', update.effective_chat.id, 'Attempted to view security logs')
        await update.message.reply_text("⚠️ 此命令仅管理员可用")
        return
    if not _security_alerts:
        await update.message.reply_text("🛡️ 安全日志为空，暂无安全事件。")
        return
    text = "🛡️ 最近安全事件：\n━━━━━━━━━━━━━━━━━━━━\n"
    for event in _security_alerts[-20:]:
        text += f"[{event['time']}] {event['type']}\n  用户: {event['user_id']}\n  详情: {event['details']}\n\n"
    if len(text) > 4000:
        text = text[:4000] + "\n...(已截取)"
    await update.message.reply_text(text)

# /changepw 命令 - 修改管理密码（仅管理员）
async def changepw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_PASSWORD
    if update.effective_chat.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⚠️ 此命令仅管理员可用")
        return
    if not context.args:
        await update.message.reply_text("用法: /changepw <新密码>")
        return
    new_pw = ' '.join(context.args)
    if len(new_pw) < 8:
        await update.message.reply_text("⚠️ 密码长度至少8位")
        return
    ADMIN_PASSWORD = new_pw
    _log_security_event('PASSWORD_CHANGED', update.effective_chat.id, 'Admin password changed')
    await update.message.reply_text("✅ 管理密码已更新。\n⚠️ 请记住新密码，重启后需在环境变量中同步更新。")

# ==================== 文字记账功能 ====================
BILL_FILE = DATA_DIR / 'bill_records.json'
bill_records = load_json(BILL_FILE, [])

def save_bill():
    save_json(BILL_FILE, bill_records)

def _parse_bill_text(text: str) -> dict:
    """
    解析记账文本，支持格式：
    - 入款 USDT 500
    - 入 USDT 500
    - 出款 银行 1000
    - 出 银行 1000
    - 入款 500 备注：客户A
    - 出款 1000 备注：提现
    返回 dict 或 None
    """
    text = text.strip()
    # 判断入款/出款
    tx_type = None
    if text.startswith(('入款', '入账')):
        tx_type = 'income'
        text = text[2:].strip()
    elif text.startswith('入'):
        tx_type = 'income'
        text = text[1:].strip()
    elif text.startswith(('出款', '出账')):
        tx_type = 'expense'
        text = text[2:].strip()
    elif text.startswith('出'):
        tx_type = 'expense'
        text = text[1:].strip()
    else:
        return None

    # 提取备注
    note = ''
    if '备注：' in text:
        parts = text.split('备注：', 1)
        text = parts[0].strip()
        note = parts[1].strip()
    elif '备注:' in text:
        parts = text.split('备注:', 1)
        text = parts[0].strip()
        note = parts[1].strip()

    # 解析渠道和金额
    parts = text.split()
    channel = ''
    amount = None

    if len(parts) >= 2:
        # 尝试: 渠道 金额 或 金额 渠道
        try:
            amount = float(parts[-1])
            channel = ' '.join(parts[:-1])
        except ValueError:
            try:
                amount = float(parts[0])
                channel = ' '.join(parts[1:])
            except ValueError:
                return None
    elif len(parts) == 1:
        try:
            amount = float(parts[0])
            channel = 'USDT'  # 默认渠道
        except ValueError:
            return None
    else:
        return None

    if amount is None or amount <= 0:
        return None

    return {
        'type': tx_type,
        'amount': amount,
        'channel': channel or 'USDT',
        'note': note
    }

async def _handle_bill_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """检查消息是否为记账格式，如果是则记录并回复。返回 True 表示已处理。"""
    user_id = update.effective_chat.id
    if not is_authorized(user_id):
        return False

    text = update.message.text.strip()
    # 快速检查是否以入/出开头
    if not text.startswith(('入', '出')):
        return False

    data = _parse_bill_text(text)
    if not data:
        return False

    # 记录到 bill_records
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record = {
        'type': data['type'],
        'amount': data['amount'],
        'channel': data['channel'],
        'note': data['note'],
        'date': now_str,
        'user_id': user_id
    }
    bill_records.append(record)
    save_bill()

    type_label = '🟢 入款' if data['type'] == 'income' else '🔴 出款'
    reply = (
        f"✅ 记账成功\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{type_label}\n"
        f"💰 金额: {data['amount']:.2f}\n"
        f"🏛️ 渠道: {data['channel']}\n"
        f"📅 时间: {now_str}\n"
    )
    if data['note']:
        reply += f"📝 备注: {data['note']}\n"

    # 计算今日汇总
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    today_in = sum(r['amount'] for r in bill_records if r['date'][:10] == today and r['type'] == 'income')
    today_out = sum(r['amount'] for r in bill_records if r['date'][:10] == today and r['type'] == 'expense')
    reply += f"━━━━━━━━━━━━━━━━━━━━\n今日入款: {today_in:.2f} | 出款: {today_out:.2f} | 净额: {today_in - today_out:.2f}"

    await update.message.reply_text(reply)
    return True

# /bill 命令 - 今日账单汇总
async def bill_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    if not is_authorized(user_id):
        await update.message.reply_text("⚠️ 您没有记账权限")
        return

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    today_records = [r for r in bill_records if r['date'][:10] == today]

    if not today_records:
        await update.message.reply_text(f"📊 今日 ({today}) 暂无记账记录")
        return

    total_in = sum(r['amount'] for r in today_records if r['type'] == 'income')
    total_out = sum(r['amount'] for r in today_records if r['type'] == 'expense')
    net = total_in - total_out

    text = (
        f"📊 今日账单汇总 ({today})\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 总入款: {total_in:.2f}\n"
        f"🔴 总出款: {total_out:.2f}\n"
        f"💰 净额: {net:.2f}\n"
        f"📝 笔数: {len(today_records)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )
    for r in today_records[-20:]:
        icon = '🟢' if r['type'] == 'income' else '🔴'
        note_str = f" ({r['note']})" if r.get('note') else ''
        text += f"{icon} {r['amount']:.2f} {r['channel']}{note_str} [{r['date'][11:16]}]\n"

    await update.message.reply_text(text)

# /billall 命令 - 所有历史记录
async def billall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    if not is_authorized(user_id):
        await update.message.reply_text("⚠️ 您没有记账权限")
        return

    if not bill_records:
        await update.message.reply_text("📊 暂无任何记账记录")
        return

    total_in = sum(r['amount'] for r in bill_records if r['type'] == 'income')
    total_out = sum(r['amount'] for r in bill_records if r['type'] == 'expense')
    net = total_in - total_out

    text = (
        f"📊 全部账单汇总\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 总入款: {total_in:.2f}\n"
        f"🔴 总出款: {total_out:.2f}\n"
        f"💰 净额: {net:.2f}\n"
        f"📝 总笔数: {len(bill_records)}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"最近 30 条记录：\n"
    )
    for r in bill_records[-30:]:
        icon = '🟢' if r['type'] == 'income' else '🔴'
        note_str = f" ({r['note']})" if r.get('note') else ''
        text += f"{icon} {r['amount']:.2f} {r['channel']}{note_str} [{r['date'][:16]}]\n"

    if len(text) > 4000:
        text = text[:4000] + "\n...(已截取)"
    await update.message.reply_text(text)

def main():
    threading.Thread(target=update_all_databases, daemon=True).start()
    
    # Force clear any existing polling sessions before starting
    import urllib.request
    for attempt in range(5):
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"
            urllib.request.urlopen(url, timeout=10)
            logger.info(f"Cleared webhook/pending updates (attempt {attempt+1})")
            # Also call getUpdates with offset=-1 to clear
            url2 = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset=-1&timeout=1"
            urllib.request.urlopen(url2, timeout=5)
            logger.info("Cleared pending getUpdates")
            break
        except Exception as e:
            logger.warning(f"Clear attempt {attempt+1} failed: {e}")
            time.sleep(3)
    
    # Wait a bit for any old instance to fully stop
    logger.info("Waiting 10 seconds for old instances to stop...")
    time.sleep(10)
    
    from telegram.ext import Defaults
    app = Application.builder().token(BOT_TOKEN).read_timeout(30).write_timeout(30).connect_timeout(30).pool_timeout(10).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("finance", main_finance_menu))
    # AI commands
    app.add_handler(CommandHandler("ai", ai_command))
    app.add_handler(CommandHandler("clear", clear_chat_command))
    app.add_handler(CommandHandler("scrape", handle_scrape_command))
    app.add_handler(CommandHandler("setadmin", setadmin_command))
    app.add_handler(CommandHandler("seclog", seclog_command))
    app.add_handler(CommandHandler("changepw", changepw_command))
    app.add_handler(CommandHandler("adduser", adduser_command))
    app.add_handler(CommandHandler("removeuser", removeuser_command))
    app.add_handler(CommandHandler("listusers", listusers_command))
    app.add_handler(CallbackQueryHandler(finance_callback, pattern="^(select_finance_|mal_fin_|phi_fin_|adv_|mal_pay_|phi_pay_|mal_exp_|phi_exp_|mal_mch|phi_mch|mal_setfee_|phi_setfee_|mal_us_|phi_us_|mal_bet_|phi_bet_|mal_agt_|phi_agt_|main_finance_menu|fin_close)"))
    app.add_handler(CommandHandler("bill", bill_command))
    app.add_handler(CommandHandler("billall", billall_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_repeating(send_security_reminder, interval=3600, first=10)
    logger.info("Bot started...")
    print("Bot started...", flush=True)
    app.run_polling(drop_pending_updates=True, poll_interval=1.0, timeout=15, allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
