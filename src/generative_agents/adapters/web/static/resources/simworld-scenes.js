(function(){
  'use strict';
  const base='/api/studio/simworld/scenes', prefix='simworld:';
  const requests=new WeakMap();
  async function responseData(response) {
    const data=await response.json();
    if (!response.ok) throw Error(typeof data.detail==='string'?data.detail:(data.detail?.message||`场景服务请求失败 (${response.status})`));
    return data;
  }
  function showCatalogStatus(status, message) {
    status.textContent=message;
    status.hidden=!message;
  }
  async function list() {
    const response=await fetch(base,{cache:'no-store',signal:AbortSignal.timeout(6500)});
    return (await responseData(response)).scenes.filter(row=>row.previewable);
  }
  async function populate(select,status) {
    const token=Symbol(); requests.set(select,token);
    const previous=select.value;
    select.querySelector('optgroup[data-simworld]')?.remove();
    showCatalogStatus(status,'正在读取 SimWorld 场景目录…');
    const group=document.createElement('optgroup'); group.label='SimWorld 场景目录'; group.dataset.simworld=''; select.append(group);
    try {
      const available=await list();
      if (requests.get(select)!==token || !group.isConnected) return;
      for (const row of available) {
        const opt=new Option(row.name,prefix+row.id); opt.scene=row; group.append(opt);
        if (previous===opt.value) opt.selected=true;
      }
      showCatalogStatus(status,available.length?'':'当前没有可预览的 SimWorld 场景。');
    } catch {
      if (requests.get(select)!==token || !group.isConnected) return;
      showCatalogStatus(status,'暂时无法连接 SimWorld 场景目录。');
    }
  }
  function selection(value) {
    return value.startsWith(prefix) ? {simworld_scene_id:value.slice(prefix.length)} : {map_id:value};
  }
  window.SimWorldScenes={list,populate,selection};
})();
