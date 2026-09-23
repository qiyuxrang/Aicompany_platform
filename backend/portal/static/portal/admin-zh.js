'use strict';

{
    const catalog = window.django && window.django.catalog;
    if (catalog) {
        Object.assign(catalog, {
            'Choose %s by selecting them and then select the "Choose" arrow button.': '选择%s后，点击向右箭头移入已选列表。',
            'Choose all %s': '选择全部%s',
            'Choose selected %s': '选择选中的%s',
            'Remove selected %s': '移除选中的%s',
            'Remove %s by selecting them and then select the "Remove" arrow button.': '选择%s后，点击向左箭头移回可选列表。',
            '(click to clear)': '（点击清除）',
            'Remove all %s': '移除全部%s',
            '%s selected option not visible': '有 %s 个已选项因筛选未显示',
        });
    }
}
