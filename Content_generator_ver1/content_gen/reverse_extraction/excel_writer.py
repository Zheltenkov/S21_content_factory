"""
ExcelWriterTool - заполнение Excel-шаблона данными.

Использует utils/excel_io.excel_template() для создания шаблона и openpyxl/pandas для заполнения.
"""

import io

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

from utils.excel_io import excel_template


class ExcelWriterTool:
    """Инструмент для заполнения Excel-шаблона."""

    def fill_excel_template(
        self,
        mapping: dict,
        template_path: str | None = None
    ) -> io.BytesIO:
        """
        Заполняет Excel-шаблон данными из mapping.
        
        Args:
            mapping: Словарь с данными (ключи соответствуют колонке "Параметр")
            template_path: Путь к шаблону (если None, используется excel_template())
            
        Returns:
            BytesIO буфер с заполненным Excel файлом
        """
        if not PANDAS_AVAILABLE:
            raise ImportError("Установите pandas: pip install pandas openpyxl")

        # Загружаем шаблон
        if template_path:
            template_df = pd.read_excel(template_path, engine='openpyxl')
        else:
            # Используем функцию из utils/excel_io
            template_buffer = excel_template()
            template_df = pd.read_excel(template_buffer, engine='openpyxl')

        # Заполняем значения
        value_col = "Значение" if "Значение" in template_df.columns else template_df.columns[1]

        for idx, row in template_df.iterrows():
            param = str(row["Параметр"]).strip()
            if param in mapping:
                template_df.at[idx, value_col] = mapping[param]

        # Сохраняем в BytesIO
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl', mode='w') as writer:
            template_df.to_excel(writer, index=False, sheet_name='Спецификация')

        output.seek(0)
        return output

