# KeyenceCrawler Design Document

## 🎯 Mục tiêu
Tạo crawler cho website Keyence (https://www.keyence.com.vn/) tương tự OmronCrawler với các tính năng:
- Cào dữ liệu sản phẩm từ category → series → products
- Đa luồng để tăng tốc độ
- Xử lý ảnh với nền trắng + WebP conversion
- Xuất Excel với HTML specs table

## 🏗️ Architecture Overview

```
KeyenceCrawler
├── Category Page → Extract Series Links (bao gồm discontinued)
├── Series Models Page → Extract Product Links (bao gồm discontinued)  
├── Product Page → Extract Details, Specs, Images
├── Multi-threading → Parallel processing
├── Image Processing → White background + WebP
└── Output → Excel + Images per category
```

## 📊 Mapping với OmronCrawler

| Omron | Keyence | Note |
|-------|---------|------|
| `fieldset.products` | `a.prd-seriesCard-link` | Series links |
| `table.details` | Models page | Product listings |
| Product specs table | `.prd-specsTable` | Specifications |
| - | Discontinued switch | Cần click để show all |

## 🔍 HTML Structure Analysis

### 1. Category Page Structure
```html
<!-- URL: https://www.keyence.com.vn/products/sensor/photoelectric/ -->

<!-- Normal Series Links -->
<a class="prd-seriesCard-link" href="/products/sensor/photoelectric/lr-x/">
    <span class="prd-utility-heading-4 prd-utility-marginBottom-1 prd-utility-block">
        <span class="prd-seriesCard-linkLabel">Bộ cảm biến Laser CMOS kỹ thuật số</span>
    </span>
    <span class="prd-utility-body-small prd-utility-color-gray prd-utility-block">Sê-ri LR-X</span>
</a>

<!-- Discontinued Toggle Switch -->
<button class="prd-switch-button" role="switch" 
        data-controller="switch switch-discontinued"
        aria-label="Bao gồm Sê-ri đã ngừng sản xuất">
</button>

<!-- Discontinued Series Links -->
<a class="prd-seriesCardDiscontinued" href="/products/sensor/photoelectric/pk/">
    <p class="prd-seriesCardDiscontinued-title">Bộ cảm biến quang điện khoảng cách cố định</p>
    <p class="prd-utility-body-small">Sê-ri PK</p>
    <span class="prd-seriesCardDiscontinued-badge">Ngưng</span>
</a>
```

### 2. Series Models Page Structure  
```html
<!-- URL: https://www.keyence.com.vn/products/sensor/photoelectric/lr-x/models/ -->

<!-- Discontinued Models Toggle -->
<button class="prd-switch-button" role="switch"
        aria-label="Bao gồm các mẫu ngừng sản xuất"
        data-controller="switch switch-discontinued">
</button>

<!-- Product Count Display -->
<span id="js-outputModelsData-totalNumber">38</span> Sản phẩm
```

### 3. Product Page Structure
```html
<!-- URL: https://www.keyence.com.vn/products/sensor/photoelectric/lr-x/models/lr-x100/ -->

<!-- Product Name Components -->
<a class="prd-inlineLink" href="/products/sensor/photoelectric/">
    <span class="prd-inlineLink-label">Cảm biến quang điện</span>
</a>
<span class="prd-utility-body-medium prd-utility-block">LR-X100</span>
<span class="prd-utility-heading-1">Dòng tiêu chuẩn, Loại cáp, tầm hoạt động 100mm</span>

<!-- Product Image -->
<img class="prd-modelIntroduction-image prd-image" 
     src="/img/products/model/AS_115939_L.jpg" 
     alt="LR-X100 - Dòng tiêu chuẩn, Loại cáp, tầm hoạt động 100mm">

<!-- Specifications Table -->
<div class="prd-specsTable" tabindex="0">
    <div class="specTable-block">
        <table class="specTable-stibo-3282397">
            <tbody>
                <tr class="specTable-row">
                    <td class="specTable-clm-0" colspan="4">Mẫu</td>
                    <td class="specTable-clm-4">LR-X100</td>
                </tr>
                <!-- More specs rows... -->
            </tbody>
        </table>
    </div>
</div>
```

## 🚀 Implementation Plan

### Phase 1: Core Structure
- [x] Create base KeyenceCrawler class
- [ ] Implement category series extraction
- [ ] Implement discontinued series handling
- [ ] Implement series models extraction

### Phase 2: Product Processing  
- [ ] Implement product details extraction
- [ ] Implement specs table parsing
- [ ] Implement product name composition
- [ ] Implement image processing with white background

### Phase 3: Output & Optimization
- [ ] Implement multi-threading
- [ ] Implement Excel output with HTML table
- [ ] Implement folder structure per category
- [ ] Implement filename standardization (JS → Python)

### Phase 4: Testing & Refinement
- [ ] Test with photoelectric category
- [ ] Test with multiple categories
- [ ] Performance optimization
- [ ] Error handling improvements

## 📝 Data Flow

```
Input: Category URL
    ↓
1. Extract Series Links (normal + discontinued)
    ↓
2. For each series → Models page
    ↓
3. Extract Product Links (normal + discontinued)
    ↓
4. For each product → Product details
    ↓
5. Compose: Name + Code + Specs + Image
    ↓
6. Process Image (white bg + WebP)
    ↓
7. Generate Excel + Save images
    ↓
Output: Category folder with Excel + images
```

## 🎨 Output Structure

```
KeyenceProducts_DDMMYYYY_HHMMSS/
├── Photoelectric/
│   ├── Photoelectric.xlsx
│   └── images/
│       ├── LR-X100.webp
│       ├── LR-X200.webp
│       └── ...
├── Proximity_Sensors/
│   ├── Proximity_Sensors.xlsx
│   └── images/
│       └── ...
└── ...
```

## 🔧 Key Differences from Omron

1. **Discontinued Products**: Keyence requires clicking switches to show all products
2. **Product Name**: Composed from multiple elements (category + model + description + brand)
3. **Specs Table**: Different HTML structure, more complex parsing needed
4. **Image Processing**: Requires white background addition
5. **URL Structure**: Different pattern (/models/ suffix for product listings)

## 📋 Implementation Checklist

- [ ] Base crawler structure
- [ ] Selenium integration for switch clicking
- [ ] Series extraction (normal + discontinued)
- [ ] Product extraction from models pages
- [ ] Product details parsing
- [ ] Specs table HTML generation
- [ ] Image processing with white background
- [ ] Filename standardization (Python version)
- [ ] Multi-threading implementation
- [ ] Excel output with proper formatting
- [ ] Folder structure management
- [ ] Error handling & logging
- [ ] Testing with real data
