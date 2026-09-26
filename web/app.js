const FILTERS = [
  ['Оригинал', '✧'], ['Ч/Б', '◑'], ['Сепия', '◒'], ['Яркий', '✦'],
  ['Мягкий', '◌'], ['Холодный', '❄'], ['Тёплый', '☀'], ['Резкость', '◇']
];

function imageCountLabel(count) {
  const ending = count % 10 === 1 && count % 100 !== 11 ? 'е' :
    [2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100) ? 'я' : 'й';
  return `${count} изображени${ending}`;
}

class ApiClient {
  async request(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let detail = 'Не удалось выполнить действие';
      try { detail = (await response.json()).error || detail; } catch (_) {}
      throw new Error(detail);
    }
    return response.json();
  }
  list() { return this.request('/api/images'); }
  upload(file) {
    return this.request(`/api/images?name=${encodeURIComponent(file.name)}`, {
      method: 'POST', headers: {'Content-Type': file.type || 'application/octet-stream'}, body: file
    });
  }
  update(id, payload) {
    return this.request(`/api/images/${id}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
    });
  }
  remove(id) { return this.request(`/api/images/${id}`, {method: 'DELETE'}); }
}

class LumaApp {
  constructor() {
    this.api = new ApiClient();
    this.images = [];
    this.activeId = null;
    this.revision = 0;
    this.editQueue = Promise.resolve();
    this.toastTimer = null;
    this.elements = Object.fromEntries([...document.querySelectorAll('[id]')].map(el => [el.id, el]));
    this.buildFilters();
    this.bindEvents();
    this.refresh();
  }

  get active() { return this.images.find(image => image.id === this.activeId) || null; }

  buildFilters() {
    this.elements.filters.innerHTML = '';
    FILTERS.forEach(([name, icon]) => {
      const control = document.createElement('button');
      control.className = 'filter-button';
      control.type = 'button';
      control.dataset.filter = name;
      control.textContent = `${icon}  ${name}`;
      control.addEventListener('click', () => this.edit({action: 'filter', name}));
      this.elements.filters.append(control);
    });
  }

  bindEvents() {
    const e = this.elements;
    for (const id of ['addBtn', 'emptyAddBtn']) e[id].addEventListener('click', () => e.fileInput.click());
    e.fileInput.addEventListener('change', async event => {
      await this.uploadFiles([...event.target.files]);
      e.fileInput.value = '';
    });
    const zone = e.dropZone;
    for (const eventName of ['dragenter', 'dragover']) zone.addEventListener(eventName, event => {
      event.preventDefault(); zone.classList.add('drag-over');
    });
    for (const eventName of ['dragleave', 'drop']) zone.addEventListener(eventName, event => {
      event.preventDefault(); zone.classList.remove('drag-over');
    });
    zone.addEventListener('drop', event => this.uploadFiles([...event.dataTransfer.files]));
    document.addEventListener('dragover', event => event.preventDefault());
    document.addEventListener('drop', event => {
      event.preventDefault();
      if (!zone.contains(event.target)) this.uploadFiles([...event.dataTransfer.files]);
    });
    e.undoBtn.addEventListener('click', () => this.edit({action: 'undo'}));
    e.redoBtn.addEventListener('click', () => this.edit({action: 'redo'}));
    e.rotateLeftBtn.addEventListener('click', () => this.edit({action: 'rotate', degrees: -90}));
    e.rotateRightBtn.addEventListener('click', () => this.edit({action: 'rotate', degrees: 90}));
    e.resetBtn.addEventListener('click', () => this.edit({action: 'reset'}));
    e.resizeBtn.addEventListener('click', () => this.resize());
    e.removeBtn.addEventListener('click', () => this.removeActive());
    for (const id of ['saveBtn', 'exportBtn']) e[id].addEventListener('click', () => this.exportActive());
    for (const key of ['brightness', 'contrast', 'saturation']) {
      e[key].addEventListener('input', () => e[`${key}Value`].textContent = `${e[key].value}%`);
      e[key].addEventListener('change', () => this.edit({action: 'adjust', [key]: Number(e[key].value)}));
    }
    e.widthInput.addEventListener('input', () => this.syncAspect('width'));
    e.heightInput.addEventListener('input', () => this.syncAspect('height'));
    document.addEventListener('keydown', event => {
      if (!(event.ctrlKey || event.metaKey)) return;
      const key = event.key.toLowerCase();
      if (['o', 's', 'z', 'y'].includes(key)) event.preventDefault();
      if (key === 'o') e.fileInput.click();
      if (key === 's') this.exportActive();
      if (key === 'z') this.edit({action: 'undo'});
      if (key === 'y') this.edit({action: 'redo'});
    });
  }

  async refresh() {
    try {
      this.images = await this.api.list();
      if (!this.images.some(image => image.id === this.activeId)) this.activeId = this.images.at(-1)?.id || null;
      this.render();
    } catch (error) { this.toast(error.message); }
  }

  async uploadFiles(files) {
    const candidates = files.filter(file => file.type.startsWith('image/') || /\.(png|jpe?g|webp|bmp|tiff?|gif)$/i.test(file.name));
    if (!candidates.length) return this.toast('Выберите файл изображения');
    const errors = [];
    for (const file of candidates) {
      try {
        const image = await this.api.upload(file);
        this.images.push(image);
        this.activeId = image.id;
      } catch (error) { errors.push(`${file.name}: ${error.message}`); }
    }
    this.render();
    this.toast(errors.length ? errors.join('; ') : `Добавлено изображений: ${candidates.length}`);
  }

  edit(payload) {
    if (!this.active) return Promise.resolve();
    const id = this.activeId;
    const apply = async () => {
      try {
        const updated = await this.api.update(id, payload);
        const index = this.images.findIndex(image => image.id === id);
        if (index >= 0) this.images[index] = updated;
        this.revision++;
        this.render();
      } catch (error) { this.toast(error.message); }
    };
    this.editQueue = this.editQueue.then(apply, apply);
    return this.editQueue;
  }

  syncAspect(changed) {
    if (!this.elements.ratioLock.checked || !this.active) return;
    const [w, h] = this.active.output_size;
    const input = changed === 'width' ? this.elements.widthInput : this.elements.heightInput;
    const other = changed === 'width' ? this.elements.heightInput : this.elements.widthInput;
    const value = Number(input.value);
    if (value > 0) other.value = Math.max(1, Math.round(changed === 'width' ? value * h / w : value * w / h));
  }

  resize() {
    const width = Number(this.elements.widthInput.value);
    const height = Number(this.elements.heightInput.value);
    if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 ||
        width > 20000 || height > 20000 || width * height > 100000000) {
      return this.toast('Размер: от 1 до 20 000 px, максимум 100 млн пикселей');
    }
    this.edit({action: 'resize', width, height});
  }

  async removeActive() {
    if (!this.active) return;
    const id = this.activeId;
    try {
      await this.editQueue;
      await this.api.remove(id);
      this.images = this.images.filter(image => image.id !== id);
      this.activeId = this.images.at(-1)?.id || null;
      this.render();
      this.toast('Изображение убрано из библиотеки');
    } catch (error) { this.toast(error.message); }
  }

  exportActive() {
    if (!this.active) return this.toast('Сначала добавьте изображение');
    const format = this.elements.formatSelect.value;
    const link = document.createElement('a');
    link.href = `/api/images/${this.activeId}/export?format=${encodeURIComponent(format)}`;
    link.download = `${this.active.name.replace(/\.[^.]+$/, '')}_edited.${format}`;
    document.body.append(link);
    link.click();
    link.remove();
    this.toast(`Экспорт в ${format.toUpperCase()} начат`);
  }

  render() {
    const e = this.elements;
    const count = this.images.length;
    e.imageCount.textContent = imageCountLabel(count);
    e.listCount.textContent = String(count).padStart(2, '0');
    e.imageList.replaceChildren();
    this.images.forEach(image => {
      const card = document.createElement('button');
      card.className = `image-card${image.id === this.activeId ? ' active' : ''}`;
      card.type = 'button';
      const thumb = document.createElement('img');
      thumb.className = 'image-thumb';
      thumb.alt = '';
      thumb.src = `/api/images/${image.id}/preview?w=100&h=100&r=${this.revision}`;
      const info = document.createElement('div');
      info.style.minWidth = '0';
      const name = document.createElement('div');
      name.className = 'image-name';
      name.textContent = image.name;
      name.title = image.name;
      const dimensions = document.createElement('div');
      dimensions.className = 'image-dims';
      dimensions.textContent = `${image.output_size[0]} × ${image.output_size[1]} px`;
      info.append(name, dimensions);
      if (image.changed) {
        const changed = document.createElement('div');
        changed.className = 'image-modified';
        changed.textContent = '● ИЗМЕНЕНО';
        info.append(changed);
      }
      card.append(thumb, info);
      card.addEventListener('click', () => { this.activeId = image.id; this.render(); });
      e.imageList.append(card);
    });
    const image = this.active;
    e.emptyState.hidden = !!image;
    e.previewImage.hidden = !image;
    e.stageTitle.textContent = image ? image.name : 'Создайте свой кадр';
    e.stageMeta.textContent = image ? `${image.output_size[0]} × ${image.output_size[1]} px` : 'Готово к работе';
    e.statusText.textContent = image ? `${imageCountLabel(count)} в библиотеке` : 'Добавьте изображение, чтобы начать';
    if (image) {
      e.previewImage.src = `/api/images/${image.id}/preview?w=1600&h=1400&r=${this.revision}`;
      for (const key of ['brightness', 'contrast', 'saturation']) {
        e[key].value = image.state[key];
        e[`${key}Value`].textContent = `${image.state[key]}%`;
      }
      e.widthInput.value = image.output_size[0];
      e.heightInput.value = image.output_size[1];
    } else e.previewImage.removeAttribute('src');
    [...e.filters.children].forEach(control => control.classList.toggle('selected', image ? control.dataset.filter === image.state.filter_name : control.dataset.filter === 'Оригинал'));
    for (const id of ['saveBtn','exportBtn','rotateLeftBtn','rotateRightBtn','resetBtn','resizeBtn','removeBtn','widthInput','heightInput','brightness','contrast','saturation','formatSelect']) e[id].disabled = !image;
    e.undoBtn.disabled = !image?.can_undo;
    e.redoBtn.disabled = !image?.can_redo;
    [...e.filters.children].forEach(control => control.disabled = !image);
  }

  toast(message) {
    const node = this.elements.toast;
    node.textContent = message;
    node.classList.add('show');
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => node.classList.remove('show'), 3500);
  }
}

window.addEventListener('DOMContentLoaded', () => { window.lumaApp = new LumaApp(); });
