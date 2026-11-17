import asyncio
from decimal import Decimal, ROUND_DOWN
import unittest


async def recommend_price(fetch_offers, current_price, min_price, timeout_s=2.0):
    """
    Рассчитывает рекомендуемую цену на основе предложений конкурентов с учетом правил:
    1. Ограничение по тайм-ауту и проверке статуса источника.
    2. Фильтрация по сроку доставки (<= 6 дней) и исключение своего магазина (MYSHOP).
    3. Обработка ситуации отсутствия подходящих предложений.
    4. Расчет цены со снижением на 1.5% и ограничением по min_price.
    5. Проверка отклонения более чем на 30% для выбора статуса (APPLY / REVIEW).
    """
    # ПРАВИЛО 1: Ограничение ожидания fetch_offers значением timeout_s.
    # Обработка исключений (тайм-аут, ошибки сети/источника).
    try:
        result = await asyncio.wait_for(fetch_offers(), timeout=timeout_s)
    except (asyncio.TimeoutError, Exception):
        return {
            "price": current_price,
            "decision": "HOLD",
            "reason": "Ошибка источника данных или превышено время ожидания (timeout)"
        }

    # ПРАВИЛО 1 (продолжение): Проверка статуса ответа

    if not isinstance(result, dict) or result.get("status") != "success":
        return {
            "price": current_price,
            "decision": "HOLD",
            "reason": f"Статус источника не успешен: {result.get('status') if isinstance(result, dict) else 'некорректный формат'}"
        }

    offers = result.get("offers", [])

    valid_prices = []

    for offer in offers:
        seller = offer.get("seller", "")
        delivery_days = offer.get("delivery_days")
        price_val = offer.get("price")

        # ПРАВИЛО 2: Исключаем собственный магазин MYSHOP независимо от регистра букв
        if seller.lower() == "myshop":
            continue

        # ПРАВИЛО 2: Учитываем предложения с известной доставкой не более 6 дней
        # (delivery_days должно быть целым числом, неотрицательным, и <= 6)
        if delivery_days is not None and delivery_days <= 6:
            if price_val is not None:
                valid_prices.append(Decimal(str(price_val)))

    # ПРАВИЛО 3: Если подходящих предложений нет, верните текущую цену и HOLD,
    # даже если текущая цена ниже min_price.
    if not valid_prices:
        return {
            "price": current_price,
            "decision": "HOLD",
            "reason": "Нет подходящих предложений от конкурентов"
        }

    # ПРАВИЛО 4: Минимальную цену подходящего конкурента уменьшаем на 1.5%
    min_competitor_price = min(valid_prices)
    
    # Расчет: min_competitor_price * (1 - 0.015) = min_competitor_price * 0.985
    discounted_price = min_competitor_price * Decimal("0.985")
    
    # Округление вниз до копеек (две значащих цифры после запятой)
    rounded_discounted_price = discounted_price.quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    # Рекомендация — максимум из этой суммы и min_price
    recommended_price = max(rounded_discounted_price, min_price)

    # ПРАВИЛО 5: Если рекомендация отличается от текущей цены более чем на 30% в любую сторону,
    # верните рекомендацию и REVIEW. Иначе — рекомендацию и APPLY. Ровно 30% допускается.
    # Формула отклонения: abs(recommended - current) / current > 0.30  ИЛИ  abs(recommended - current) > current * 0.30
    diff = abs(recommended_price - current_price)
    threshold = current_price * Decimal("0.30")

    if diff > threshold:
        decision = "REVIEW"
        reason = "Отклонение от текущей цены превышает 30%, требуется ручная проверка"
    else:
        decision = "APPLY"
        reason = "Цена рассчитана успешно, отклонение в пределах нормы"

    return {
        "price": recommended_price,
        "decision": decision,
        "reason": reason
    }


async def sample_fetch_offers():
    return {
        "status": "success",
        "offers": [
            {"seller": "myshop", "price": Decimal("4300.00"), "delivery_days": 2},
            {"seller": "PARTS", "price": Decimal("4800.00"), "delivery_days": 3},
            {"seller": "SLOW", "price": Decimal("4700.00"), "delivery_days": 8},
        ],
    }


# АВТОМАТИЧЕСКИЕ ТЕСТЫ
class TestRecommendPrice(unittest.TestCase):
    
    def test_normal_calculation(self):
        """Тест 1: Обычный расчет. 
        Конкуренты: PARTS (4800.00, 3 дня) -> подходит. 
        MYSHOP исключается. SLOW (8 дней) исключается.
        Расчет: 4800.00 * 0.985 = 4728.00. 
        Сравнение с min_price (4500.00) -> 4728.00.
        Отклонение от текущей (4900.00): abs(4728 - 4900) = 172. 
        Порог 30% от 4900 = 1470 (отклонение меньше), значит APPLY."""
        async def mock_fetch():
            return {
                "status": "success",
                "offers": [
                    {"seller": "myshop", "price": Decimal("4300.00"), "delivery_days": 2},
                    {"seller": "PARTS", "price": Decimal("4800.00"), "delivery_days": 3},
                    {"seller": "SLOW", "price": Decimal("4700.00"), "delivery_days": 8},
                ],
            }

        result = asyncio.run(recommend_price(
            mock_fetch,
            current_price=Decimal("4900.00"),
            min_price=Decimal("4500.00"),
        ))
        
        self.assertEqual(result["price"], Decimal("4728.00"))
        self.assertEqual(result["decision"], "APPLY")

    def test_timeout_or_error_case(self):
        """Тест 2: Сбой/тайм-аут источника (Правило 1).
        Функция зависает дольше timeout_s или возвращает ошибку статуса."""
        async def mock_fetch_slow():
            await asyncio.sleep(0.5)
            return {"status": "success", "offers": []}

        result = asyncio.run(recommend_price(
            mock_fetch_slow,
            current_price=Decimal("4900.00"),
            min_price=Decimal("4500.00"),
            timeout_s=0.1  # Спровоцируем тайм-аут
        ))
        
        self.assertEqual(result["price"], Decimal("4900.00"))
        self.assertEqual(result["decision"], "HOLD")


if __name__ == "__main__":
    # Запуск демонстрационного примера из условий
    print("Результат примера:", asyncio.run(recommend_price(
        sample_fetch_offers,
        current_price=Decimal("4900.00"),
        min_price=Decimal("4500.00"),
    )))
    

    # Запуск тестов
    print("\nЗапуск автоматических тестов...")
    
    unittest.main(argv=['first-arg-is-ignored'], exit=False)