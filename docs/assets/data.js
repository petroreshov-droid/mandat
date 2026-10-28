// Сгенерировано assets/build_data.mjs из docs/data/*.json. Не править вручную.
export const MARKET = {
  "as_of": "2026-11-10T10:00:00+03:00",
  "note": "Синтетические данные для прототипа. Цены облигаций - в валюте за одну бумагу. В MVP данные идут из песочницы T-Invest.",
  "fx": {
    "rub": 1,
    "cny": 11.2
  },
  "cash_rub": 268653,
  "instruments": {
    "TMOS": {
      "name": "Т-Капитал Индекс МосБиржи",
      "class": "etf",
      "lot": 1,
      "currency": "rub",
      "qual_only": false,
      "status": "normal",
      "last": 7.05
    },
    "SBER": {
      "name": "Сбербанк",
      "class": "share",
      "lot": 10,
      "currency": "rub",
      "qual_only": false,
      "status": "normal",
      "last": 305.4
    },
    "LKOH": {
      "name": "Лукойл",
      "class": "share",
      "lot": 1,
      "currency": "rub",
      "qual_only": false,
      "status": "normal",
      "last": 6810
    },
    "YDEX": {
      "name": "Яндекс",
      "class": "share",
      "lot": 1,
      "currency": "rub",
      "qual_only": false,
      "status": "normal",
      "last": 4120
    },
    "SU26238RMFS4": {
      "name": "ОФЗ 26238",
      "class": "bond",
      "lot": 1,
      "currency": "rub",
      "qual_only": false,
      "status": "normal",
      "last": 612.3
    },
    "SU26243RMFS4": {
      "name": "ОФЗ 26243",
      "class": "bond",
      "lot": 1,
      "currency": "rub",
      "qual_only": false,
      "status": "normal",
      "last": 745.9
    },
    "SU26248RMFS4": {
      "name": "ОФЗ 26248",
      "class": "bond",
      "lot": 1,
      "currency": "rub",
      "qual_only": false,
      "status": "normal",
      "last": 905.1
    },
    "SU52002RMFS1": {
      "name": "ОФЗ 52002 в юанях",
      "class": "bond",
      "lot": 1,
      "currency": "cny",
      "qual_only": false,
      "status": "normal",
      "last": 984
    },
    "SiZ6": {
      "name": "Фьючерс USD/RUB, декабрь 2026",
      "class": "futures",
      "lot": 1,
      "currency": "rub",
      "qual_only": false,
      "status": "normal",
      "last": 84500
    },
    "STRUCT1": {
      "name": "Структурная облигация (демо)",
      "class": "bond",
      "lot": 1,
      "currency": "rub",
      "qual_only": true,
      "status": "normal",
      "last": 1000
    }
  },
  "positions": {
    "TMOS": 19000,
    "SBER": 400,
    "LKOH": 17,
    "SU26238RMFS4": 200,
    "SU26243RMFS4": 160,
    "SU26248RMFS4": 130
  },
  "books": {
    "TMOS": {
      "asks": [
        [
          7.06,
          250000
        ],
        [
          7.07,
          400000
        ]
      ],
      "bids": [
        [
          7.05,
          300000
        ],
        [
          7.04,
          350000
        ]
      ]
    },
    "SBER": {
      "asks": [
        [
          305.5,
          8000
        ],
        [
          305.6,
          12000
        ]
      ],
      "bids": [
        [
          305.4,
          9000
        ],
        [
          305.3,
          11000
        ]
      ]
    },
    "LKOH": {
      "asks": [
        [
          6812,
          900
        ],
        [
          6815,
          1500
        ]
      ],
      "bids": [
        [
          6810,
          1000
        ],
        [
          6808,
          1400
        ]
      ]
    },
    "YDEX": {
      "asks": [
        [
          4121,
          700
        ],
        [
          4123,
          1100
        ]
      ],
      "bids": [
        [
          4120,
          800
        ],
        [
          4118,
          900
        ]
      ]
    },
    "SU26238RMFS4": {
      "asks": [
        [
          612.5,
          3000
        ],
        [
          612.8,
          5000
        ]
      ],
      "bids": [
        [
          612.3,
          3500
        ],
        [
          612,
          4000
        ]
      ]
    },
    "SU26243RMFS4": {
      "asks": [
        [
          746.4,
          25
        ],
        [
          748,
          35
        ]
      ],
      "bids": [
        [
          745.9,
          30
        ],
        [
          744.5,
          40
        ]
      ]
    },
    "SU26248RMFS4": {
      "asks": [
        [
          905.6,
          1500
        ],
        [
          906.2,
          2400
        ]
      ],
      "bids": [
        [
          905.1,
          1600
        ],
        [
          904.8,
          2000
        ]
      ]
    },
    "SU52002RMFS1": {
      "asks": [
        [
          984.5,
          200
        ]
      ],
      "bids": [
        [
          984,
          180
        ]
      ]
    },
    "SiZ6": {
      "asks": [
        [
          84510,
          5000
        ]
      ],
      "bids": [
        [
          84500,
          5200
        ]
      ]
    },
    "STRUCT1": {
      "asks": [
        [
          1000,
          100
        ]
      ],
      "bids": [
        [
          999,
          90
        ]
      ]
    }
  }
};

export const MANDATES = {
  "t1": {
    "schema": "mandat/v1",
    "title": "Облигации, фонд и реинвест",
    "client_text": "Покупай только из моего списка: ОФЗ 26238, 26243 и 26248, фонд TMOS, Сбер и Лукойл. Купоны реинвестируй в ту ОФЗ, где моя доля меньше. Каждое 10-е число докупай TMOS на 10 000 ₽. Не больше 15% в одной бумаге, без плеча и без продаж. Заявки больше 30 000 ₽ - только с моего подтверждения.",
    "allowed_classes": [
      "bond",
      "etf",
      "share"
    ],
    "allowed_tickers": [
      "SU26238RMFS4",
      "SU26243RMFS4",
      "SU26248RMFS4",
      "TMOS",
      "SBER",
      "LKOH"
    ],
    "strict_whitelist": false,
    "allowed_currencies": [
      "rub"
    ],
    "allow_sell": false,
    "forbid_leverage": true,
    "forbid_qual_only": true,
    "limits": {
      "max_position_share": 0.15,
      "max_order_value": 50000,
      "daily_turnover": null,
      "ask_above": 30000,
      "max_price_deviation": 0.01,
      "max_book_share": 0.2,
      "max_orders_per_minute": 5
    },
    "standing_orders": [
      {
        "id": "reinvest",
        "on": "coupon",
        "action": "buy_lowest_share",
        "from": [
          "SU26238RMFS4",
          "SU26243RMFS4",
          "SU26248RMFS4"
        ]
      },
      {
        "id": "dca",
        "on": "monthly",
        "day": 10,
        "action": "buy_amount",
        "ticker": "TMOS",
        "amount_rub": 10000
      }
    ]
  },
  "t2": {
    "schema": "mandat/v1",
    "title": "Свободный мандат",
    "client_text": "Торгуй на своё усмотрение, главное заработай.",
    "allowed_classes": [
      "bond",
      "etf",
      "share",
      "futures"
    ],
    "allowed_tickers": [],
    "strict_whitelist": false,
    "allowed_currencies": [
      "rub",
      "cny"
    ],
    "allow_sell": true,
    "forbid_leverage": false,
    "forbid_qual_only": false,
    "limits": {
      "max_position_share": null,
      "max_order_value": null,
      "daily_turnover": null,
      "ask_above": null,
      "max_price_deviation": null,
      "max_book_share": null,
      "max_orders_per_minute": null
    },
    "standing_orders": []
  }
};
