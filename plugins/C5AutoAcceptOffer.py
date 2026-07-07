import time

from PyC5Game import C5Account
from steampy.models import TradeOfferState
from utils.logger import PluginLogger, handle_caught_exception
from utils.steam_client import accept_trade_offer, external_handler

logger = PluginLogger("C5AutoAcceptOffer")


class C5AutoAcceptOffer:
    def __init__(self, steam_client, steam_client_mutex, config):
        self.steam_client = steam_client
        self.steam_client_mutex = steam_client_mutex
        self.config = config
        with steam_client_mutex:
            self.steam_id = steam_client.get_steam64id_from_cookies()

    def init(self) -> bool:
        return False

    def _get_order_page(self, status, page):
        resp = self.client.orderList(status=status, page=page, steamId=self.steam_id)
        if resp.get("errorCode", "") == 400001:
            logger.error("app_key错误，请检查配置文件内的app_key是否正确")
            logger.error("由于app_key错误，插件已停止运行")
            return None, True
        data = resp.get("data") or {}
        return data, False

    def _get_orders_by_status(self, status):
        orders = []
        page = 0
        while True:
            page += 1
            data, app_key_invalid = self._get_order_page(status, page)
            if app_key_invalid:
                return None
            page_orders = data.get("list", [])
            orders.extend(page_orders)
            limit = data.get("limit", len(page_orders))
            if len(page_orders) < limit:
                break
        return orders

    def _verify_offer_matches_order(self, offer_id, order_name):
        with self.steam_client_mutex:
            trade = self.steam_client.get_trade_offer(str(offer_id), merge=True)["response"]["offer"]
        trade_state = TradeOfferState(trade["trade_offer_state"])
        if trade_state not in [TradeOfferState.Active, TradeOfferState.ConfirmationNeed]:
            logger.error(f"报价 {offer_id} 当前状态不是待处理状态，已跳过")
            return False
        items_to_give = trade.get("items_to_give") or {}
        if not items_to_give:
            logger.error(f"报价 {offer_id} 没有需要支出的物品，已跳过")
            return False

        item_names = []
        if isinstance(items_to_give, dict):
            offer_items = items_to_give.values()
        else:
            offer_items = items_to_give
        for item in offer_items:
            name = item.get("market_hash_name") or item.get("name")
            if name:
                item_names.append(name)
        if not item_names:
            logger.error(f"无法从 Steam 报价 {offer_id} 读取支出物品名称，已跳过")
            return False
        if order_name not in item_names:
            logger.error(f"报价 {offer_id} 支出物品与 C5 订单不匹配，订单：{order_name}，报价物品：{item_names}")
            return False
        return True

    def exec(self):
        ignored_list = []
        try:
            self.interval = self.config.get("c5_auto_accept_offer").get("interval")
        except Exception as e:
            logger.error("读取配置文件出错！请检查配置文件内的interval是否正确")
            return True

        app_key = self.config.get("c5_auto_accept_offer").get("app_key")
        self.client = C5Account(app_key)
        try:
            app_key_valid = self.client.checkAppKey()
        except Exception as e:
            handle_caught_exception(e, prefix="C5AutoAcceptOffer")
            logger.error("C5账号登录失败！请检查网络或配置文件内的app_key是否正确")
            return True
        if app_key_valid:
            logger.info("C5账号登录成功")
        else:
            logger.error("C5账号登录失败！请检查配置文件内的app_key是否正确")
            return True

        while True:
            try:
                logger.info("正在检索是否有待发货订单...")
                notDeliveredOrders = self._get_orders_by_status(status=1)
                if notDeliveredOrders is None:
                    return 1
                logger.info(f"共检索到{len(notDeliveredOrders)}个待发货订单")
                if notDeliveredOrders:
                    toSendOrderIds = []
                    for order in notDeliveredOrders:
                        if external_handler("C5-" + str(order["orderId"]), desc=f"发货平台：C5Game\n发货商品：{order['name']}\n订单价格：{order['price']}元"):
                            toSendOrderIds.append(order["orderId"])
                    if len(toSendOrderIds) > 0:
                        logger.info(f"正在发送 {len(toSendOrderIds)} 个报价...")
                        self.client.deliver(toSendOrderIds)
                        logger.info("已请求C5服务器发送报价，30秒后获取报价ID")
                        time.sleep(30)
                deliveringOrders = self._get_orders_by_status(status=2)
                if deliveringOrders is None:
                    return 1
                logger.info(f"共检索到{len(deliveringOrders)}个正在发货订单")
                for deliveringOrder in deliveringOrders:
                    logger.info(f"正在处理订单 {deliveringOrder['name']} ...")
                    offerId = deliveringOrder["orderConfirmInfoDTO"]["offerId"]
                    if offerId in ignored_list:
                        logger.info(f"订单 {deliveringOrder['name']} 已发货，跳过")
                        continue
                    if not self._verify_offer_matches_order(offerId, deliveringOrder["name"]):
                        continue
                    if accept_trade_offer(
                        self.steam_client,
                        self.steam_client_mutex,
                        offerId,
                        desc=f"发货平台：C5Game\n发货饰品：{deliveringOrder['name']}\n订单价格：{round(deliveringOrder['price'], 2)}",
                        reportToExternal=False,
                    ):
                        logger.info(f"订单 {deliveringOrder['name']} 发货完成")
                        ignored_list.append(offerId)
                        if deliveringOrders.index(deliveringOrder) != len(deliveringOrders) - 1:
                            logger.info("为避免频繁访问Steam接口，等待3秒后处理下一个订单")
                            time.sleep(3)
                    else:
                        logger.error(f"订单 {deliveringOrder['name']} 发货失败，请检查网络或者Steam账号！")
            except Exception as e:
                handle_caught_exception(e, prefix="C5AutoAcceptOffer")
            logger.info(f"等待{self.interval}秒后重新检索是否有待发货订单")
            time.sleep(self.interval)
