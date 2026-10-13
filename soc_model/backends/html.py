"""Generate an interactive HTML visualization of the SoC model."""

import json
from typing import Any, Dict, List, Optional

from ..model import Module
from ..utils import write_if_changed
from .base import Backend, BackendResult, GenContext, Phase
from .registry import register_backend


@register_backend
class SoCVisualizer(Backend):
    """Generates a standalone interactive HTML block diagram."""

    name = "html"
    phase = Phase.REPORT

    def __init__(self, top_module: Optional[Module] = None):
        self.top = top_module

    def run(self, ctx: GenContext) -> BackendResult:
        self.top = ctx.top
        html_path = ctx.reports_dir / f"{ctx.top.name}_connectivity.html"
        json_path = ctx.reports_dir / f"{ctx.top.name}_model_view.json"
        self.generate_html(str(html_path), ctx.messages)
        self.generate_json(str(json_path), ctx.messages)
        return BackendResult(files=[html_path, json_path])

    def build_payload(self, validation_messages: List = None) -> Dict[str, Any]:
        """Build the complete JSON-serializable model-view payload (schema v1).

        Single source of truth for the report data: the self-contained HTML,
        the standalone ``*_model_view.json`` artifact and the fetch-based
        viewer page all consume this one dict.
        """
        return {
            'meta': {
                'name': self.top.name,
                'desc': self.top.desc,
                'generator': 'nanosoc_gen',
                'schema': 1,
            },
            'graph': self._build_graph_data(),
            'all_graphs': self._build_all_graph_data(),
            'address_map': self._build_address_map(),
            'initiator_maps': self._build_initiator_maps(),
            'interconnect_info': self._build_interconnect_info(),
            'hierarchy': self._build_hierarchy(),
            'assigns': self._build_assigns_data(),
            'validation': [str(m) for m in (validation_messages or [])],
        }

    def generate_html(self, output_path: str, validation_messages: List = None):
        """Generate a self-contained HTML file with D3.js visualization."""
        payload = self.build_payload(validation_messages)
        html = _render_page(
            top_name=self.top.name,
            msg_count=len(payload['validation']),
            script_open='<script>',
            data_section=_inline_data_section(payload),
        )
        write_if_changed(output_path, html)

    def generate_json(self, output_path: str, validation_messages: List = None):
        """Write the standalone model-view JSON artifact (schema v1)."""
        payload = self.build_payload(validation_messages)
        write_if_changed(str(output_path), json.dumps(payload, indent=2) + '\n')

    def generate_viewer_html(self, output_path: str, json_url: str,
                             validation_messages: List = None):
        """Generate the viewer-mode page: the SAME report, but the data is
        fetch()ed from ``json_url`` at load instead of inlined. Needs http(s)."""
        messages = [str(m) for m in (validation_messages or [])]
        write_if_changed(
            str(output_path),
            render_viewer_html(self.top.name, json_url, len(messages)))

    def _build_graph_data(self) -> Dict[str, Any]:
        """Build nodes and links for the block diagram."""
        return self._build_module_graph(self.top)

    def _build_all_graph_data(self) -> Dict[str, Any]:
        """Build graph data for every module that has child instances, keyed by module name.

        This enables drill-down into subsystems in the connectivity visualiser.
        """
        all_graphs = {}
        self._collect_module_graphs(self.top, all_graphs)
        return all_graphs

    def _collect_module_graphs(self, module, out: Dict[str, Any]):
        """Recursively build graph data for a module and its children."""
        graph = self._build_module_graph(module)
        if graph['nodes']:  # Only include modules that have children to show
            out[module.name] = graph

        # Recurse into child instances
        for inst in module.instances:
            child = inst.resolved_module
            if child and child.instances:
                self._collect_module_graphs(child, out)

    def _classify_link_type(self, port_name: str, conn_str: str,
                             port_iface_type: str, ic_names: set) -> str:
        """Determine the link type for a connection.

        Uses the interface type from the child module when available, and falls
        back to interconnect name / port name heuristics.
        """
        # Use interface type from the child module definition
        if port_iface_type in ('ahb',):
            return 'ahb'
        if port_iface_type in ('apb',):
            return 'apb'
        if port_iface_type in ('axis', 'axis_byte'):
            return 'axis'
        if port_iface_type in ('swd',):
            return 'wire'

        # Check if the connection target is an interconnect (AHB bus connection)
        conn_base = conn_str.split('[')[0]
        if '.' in conn_base:
            target_name = conn_base.split('.', 1)[0]
            if target_name in ic_names:
                return 'ahb'

        # Heuristic fallbacks for port name patterns
        port_lower = port_name.lower()
        if port_lower.startswith(('usrt', 'adp', 'ft_adp')):
            return 'axis'
        if 'str_' in port_lower:
            return 'axis'
        if port_lower in ('hclk', 'sys_hclk', 'sys_pclk', 'sys_pclkg', 'sys_fclk', 'clk'):
            return 'clock'
        if ('reset' in port_lower or port_lower.endswith('resetn')
                or port_lower in ('sys_hresetn', 'sys_presetn', 'resetn')):
            return 'reset'

        return 'wire'

    def _build_module_graph(self, module) -> Dict[str, Any]:
        """Build nodes and links for a single module's internal connectivity."""
        nodes = []
        links = []

        # Module itself as container
        nodes.append({
            'id': module.name,
            'type': 'top',
            'label': module.name,
            'desc': module.desc,
            'interfaces': len(module.interfaces),
            'params': len(module.params),
        })

        # Build module interface lookup for port node creation
        iface_lookup = {}
        for iface in module.interfaces:
            iface_lookup[iface.name] = iface

        # Collect interconnect names for AHB link detection
        ic_names = {ic.name for ic in module.interconnects}

        for inst in module.instances:
            mod = inst.resolved_module
            node_type = 'subsystem'
            if inst.module_name.startswith('nanosoc_region'):
                node_type = 'region'
            elif inst.is_rtl_module:
                node_type = 'rtl_ip'

            iface_count = len(mod.interfaces) if mod else 0
            has_children = bool(mod and mod.instances)
            node = {
                'id': inst.instance_name,
                'type': node_type,
                'label': inst.instance_name,
                'module': inst.module_name,
                'addressable': inst.addressable,
                'interfaces': iface_count,
                'connections': len(inst.connections),
                'condition': inst.condition,
                'has_children': has_children,
            }
            nodes.append(node)

            # Build port direction and type lookups from child module interfaces
            port_dirs = {}
            port_types = {}
            if mod:
                for iface in mod.interfaces:
                    port_dirs[iface.name] = iface.direction
                    port_types[iface.name] = iface.type

            for conn in inst.connections:
                conn_str = conn.conn
                if '.' in conn_str.split('[')[0]:
                    parts = conn_str.split('[')[0].split('.', 1)
                    target_inst = parts[0]
                else:
                    target_inst = module.name
                port_base = conn.port.split('[')[0]
                port_iface_type = port_types.get(port_base, '')
                link_type = self._classify_link_type(
                    conn.port, conn_str, port_iface_type, ic_names)
                port_dir = port_dirs.get(port_base, '')
                if port_dir in ('out', 'initiator', 'sender'):
                    direction = 'out'
                elif port_dir in ('in', 'target', 'receiver'):
                    direction = 'in'
                else:
                    direction = 'bidi'
                links.append({
                    'source': inst.instance_name,
                    'target': target_inst,
                    'port': conn.port,
                    'conn': conn.conn,
                    'type': link_type,
                    'direction': direction,
                    'portDir': port_dir,
                })

        # Interconnects within this module
        for ic in module.interconnects:
            nodes.append({
                'id': ic.name,
                'type': 'interconnect',
                'label': ic.name,
                'bus_type': ic.type,
                'targets': len(ic.targets),
                'initiators': len(ic.initiators),
                'connections': len(ic.connections),
                'has_children': False,
            })
            for conn in ic.connections:
                conn_str = conn.conn
                if '.' in conn_str.split('[')[0]:
                    parts = conn_str.split('[')[0].split('.', 1)
                    target_inst = parts[0]
                else:
                    target_inst = module.name
                link_type = 'wire'
                if conn.port.lower() in ('sys_hclk', 'clk'):
                    link_type = 'clock'
                elif 'reset' in conn.port.lower() or conn.port.lower().endswith('resetn'):
                    link_type = 'reset'
                links.append({
                    'source': ic.name,
                    'target': target_inst,
                    'port': conn.port,
                    'conn': conn.conn,
                    'type': link_type,
                    'direction': 'in',
                    'portDir': 'in',
                })

        # Generate implicit links for interconnect initiators/targets that connect
        # to the module boundary (no explicit instance connection covers them).
        # Collect the set of interconnect port names already covered by explicit links.
        for ic in module.interconnects:
            covered_ic_ports = set()
            for link in links:
                conn_str = link.get('conn', '')
                conn_base = conn_str.split('[')[0]
                if '.' in conn_base:
                    ic_ref, port_ref = conn_base.split('.', 1)
                    if ic_ref == ic.name:
                        covered_ic_ports.add(port_ref)

            # Uncovered initiators → connected from module boundary port
            for init in ic.initiators:
                if init.name not in covered_ic_ports:
                    # This initiator has no explicit connection — it comes from the boundary
                    # Find the matching module interface (same name)
                    iface = iface_lookup.get(init.name)
                    port_name = init.name
                    if iface:
                        # Direction: the module interface feeds INTO the interconnect initiator
                        direction = 'in' if iface.direction in ('in', 'target', 'receiver') else 'out'
                    else:
                        direction = 'in'
                    links.append({
                        'source': module.name,
                        'target': ic.name,
                        'port': port_name,
                        'conn': port_name,
                        'type': 'ahb',
                        'direction': direction,
                        'portDir': iface.direction if iface else 'target',
                    })

            # Uncovered targets → connected to module boundary port
            for tgt in ic.targets:
                if tgt.name not in covered_ic_ports:
                    # This target has no explicit connection — it routes to the boundary
                    # Try to find a matching module interface
                    iface = iface_lookup.get(tgt.name)
                    port_name = tgt.name
                    if iface:
                        direction = 'out' if iface.direction in ('out', 'initiator', 'sender') else 'in'
                    else:
                        direction = 'out'
                    links.append({
                        'source': ic.name,
                        'target': module.name,
                        'port': port_name,
                        'conn': port_name,
                        'type': 'ahb',
                        'direction': direction,
                        'portDir': iface.direction if iface else 'initiator',
                    })

        # Create port nodes for connections to the parent module boundary.
        # Collect unique signal names that link to module.name.
        port_signals = {}  # signal_name -> link_type of first link using it
        for link in links:
            if link['target'] == module.name:
                sig = link['conn'].split('[')[0]
                if sig not in port_signals:
                    port_signals[sig] = link['type']
            elif link['source'] == module.name:
                sig = link['conn'].split('[')[0]
                if sig not in port_signals:
                    port_signals[sig] = link['type']

        # Also track which signals are used as link sources vs targets
        # to infer direction when no interface definition exists.
        port_is_source = set()  # signals that appear as link source (data flows out)
        port_is_target = set()  # signals that appear as link target (data flows in)
        for link in links:
            if link['target'] == module.name:
                port_is_target.add(link['conn'].split('[')[0])
            elif link['source'] == module.name:
                port_is_source.add(link['conn'].split('[')[0])

        port_node_ids = set()
        for sig_name, first_link_type in port_signals.items():
            port_id = f'__port_{sig_name}'
            port_node_ids.add(port_id)
            # Determine direction from the module's interface definitions
            iface = iface_lookup.get(sig_name)
            if iface:
                port_dir = iface.direction
                port_iface_type = iface.type
                port_desc = iface.desc
            else:
                port_dir = ''
                port_iface_type = ''
                port_desc = ''

            # Classify the port side: inputs come into the module, outputs go out
            if port_dir in ('in', 'target', 'receiver'):
                port_side = 'left'
            elif port_dir in ('out', 'initiator', 'sender'):
                port_side = 'right'
            elif sig_name in port_is_source and sig_name not in port_is_target:
                # Only used as source → data originates from boundary → input
                port_side = 'left'
            elif sig_name in port_is_target and sig_name not in port_is_source:
                # Only used as target → data flows to boundary → output
                port_side = 'right'
            else:
                port_side = 'left'

            nodes.append({
                'id': port_id,
                'type': 'port',
                'label': sig_name,
                'portDir': port_dir,
                'portIfaceType': port_iface_type,
                'portSide': port_side,
                'portLinkType': first_link_type,
                'desc': port_desc,
                'has_children': False,
            })

        # Redirect links from module.name to the port nodes
        for link in links:
            if link['target'] == module.name:
                sig = link['conn'].split('[')[0]
                link['target'] = f'__port_{sig}'
            elif link['source'] == module.name:
                sig = link['conn'].split('[')[0]
                link['source'] = f'__port_{sig}'

        return {'nodes': nodes, 'links': links}

    def _build_address_map(self) -> List[Dict[str, Any]]:
        """Build address map data for visualization, including nested interconnects."""
        regions = []
        self._collect_regions(self.top, 0, 0, regions)
        return sorted(regions, key=lambda r: (r['depth'], r['abs_base']))

    def _collect_regions(self, module, parent_base: int, depth: int, out: List[Dict[str, Any]]):
        """Recursively collect address regions from interconnects and address decodes."""
        for ic in module.interconnects:
            for t in ic.targets:
                abs_base = parent_base + t.base
                # Resolve a target's register_map the same way _collect_decode_
                # regions does for slots — a peripheral reached directly as a
                # target (e.g. the rendered DMA-250 at dmac_ctrl) must not be
                # invisible to the model just because it is not behind an APB
                # decode slot.
                register_map_name = None
                registers = []
                if t.resolved_register_map:
                    rm = t.resolved_register_map
                    register_map_name = rm.name
                    for reg in rm.registers:
                        registers.append({
                            'name': reg.name,
                            'offset': reg.offset,
                            'abs_addr': abs_base + reg.offset,
                            'width': reg.width,
                            'access': reg.access,
                            'desc': reg.desc,
                            'fields': [
                                {
                                    'name': f.name,
                                    'bits': f.bits,
                                    'access': f.access,
                                    'desc': f.desc,
                                }
                                for f in reg.fields
                            ],
                        })
                region = {
                    'name': t.name,
                    'instance': t.instance or '',
                    'base': t.base,
                    'abs_base': abs_base,
                    'size': t.size,
                    'end': abs_base + t.size - 1,
                    'sw_access': t.sw_access,
                    'region_type': t.region_type or '',
                    'subordinate_bus': t.subordinate_bus,
                    'desc': t.desc,
                    'interconnect': ic.name,
                    'depth': depth,
                    'children': [],
                    'register_map': register_map_name,
                    'registers': registers,
                }

                # Check if target instance has sub-interconnects or address decodes
                if t.instance:
                    inst = module.get_instance(t.instance)
                    if inst and inst.resolved_module:
                        child_regions = []
                        self._collect_module_regions(inst.resolved_module, abs_base, depth + 1,
                                                     ic.name, child_regions)
                        if child_regions:
                            region['children'] = child_regions
                            out.extend(child_regions)

                out.append(region)

    def _collect_module_regions(self, module, parent_base: int, depth: int,
                                interconnect_name: str, out: List[Dict[str, Any]]):
        """Collect regions from a module's interconnects, address_decode, and child instances."""
        if module.interconnects:
            self._collect_regions(module, parent_base, depth, out)
        if module.address_decode:
            self._collect_decode_regions(module.address_decode, parent_base, depth,
                                         interconnect_name, out)
        # Also search addressable child instances for address_decode
        if not module.interconnects and not module.address_decode:
            for inst in module.instances:
                if inst.addressable and inst.resolved_module:
                    self._collect_module_regions(inst.resolved_module, parent_base, depth,
                                                 interconnect_name, out)

    def _collect_decode_regions(self, ad, parent_base: int, depth: int,
                                interconnect_name: str, out: List[Dict[str, Any]]):
        """Recursively collect regions from address decode hierarchies."""
        for slot in ad.slots:
            abs_base = parent_base + slot.offset
            register_map_name = None
            registers = []

            if slot.resolved_register_map:
                rm = slot.resolved_register_map
                register_map_name = rm.name
                for reg in rm.registers:
                    registers.append({
                        'name': reg.name,
                        'offset': reg.offset,
                        'abs_addr': abs_base + reg.offset,
                        'width': reg.width,
                        'access': reg.access,
                        'desc': reg.desc,
                        'fields': [
                            {
                                'name': f.name,
                                'bits': f.bits,
                                'access': f.access,
                                'desc': f.desc,
                            }
                            for f in reg.fields
                        ],
                    })

            region = {
                'name': slot.name,
                'instance': slot.module,
                'base': slot.offset,
                'abs_base': abs_base,
                'size': slot.size,
                'end': abs_base + slot.size - 1,
                'sw_access': '',
                'region_type': 'decode',
                'subordinate_bus': False,
                'desc': slot.desc,
                'interconnect': interconnect_name,
                'depth': depth,
                'children': [],
                'register_map': register_map_name,
                'registers': registers,
            }

            # Recurse into nested address decode
            if slot.address_decode:
                child_regions = []
                self._collect_decode_regions(
                    slot.address_decode, abs_base, depth + 1,
                    interconnect_name, child_regions
                )
                region['children'] = child_regions
                out.extend(child_regions)

            out.append(region)

    def _build_initiator_maps(self) -> Dict[str, Any]:
        """Build per-initiator memory map data, including nested sub-regions."""
        result = {}
        for ic in self.top.interconnects:
            target_map = {t.name: t for t in ic.targets}
            for init in ic.initiators:
                init_regions = []
                for tgt_name in init.target_names:
                    t = target_map.get(tgt_name)
                    if t:
                        region = {
                            'name': t.name,
                            'instance': t.instance or '',
                            'base': t.base,
                            'size': t.size,
                            'end': t.base + t.size - 1,
                            'sw_access': t.sw_access,
                            'region_type': t.region_type or '',
                            'children': [],
                        }
                        # Add child interconnect regions
                        if t.instance:
                            inst = self.top.get_instance(t.instance)
                            if inst and inst.resolved_module:
                                for child_ic in inst.resolved_module.interconnects:
                                    for ct in child_ic.targets:
                                        region['children'].append({
                                            'name': ct.name,
                                            'base': t.base + ct.base,
                                            'size': ct.size,
                                            'end': t.base + ct.base + ct.size - 1,
                                            'sw_access': ct.sw_access,
                                            'region_type': ct.region_type or '',
                                        })
                        init_regions.append(region)
                result[init.name] = {
                    'instance': init.instance or '',
                    'regions': sorted(init_regions, key=lambda r: r['base']),
                }
        return result

    def _build_interconnect_info(self) -> List[Dict[str, Any]]:
        """Build per-interconnect info with initiators, visibility, and remap data.

        Recursively walks the module hierarchy to find all interconnects.
        For each, produces initiator target lists with visibility windows
        and remap bit specifications.
        """
        result = []
        self._collect_interconnect_info(self.top, '', 0, result)
        return result

    def _collect_interconnect_info(self, module, parent_path: str, parent_base: int,
                                    out: List[Dict[str, Any]]):
        """Recursively collect interconnect info from module hierarchy."""
        for ic in module.interconnects:
            target_map = {t.name: t for t in ic.targets}
            ic_path = f"{parent_path}.{ic.name}" if parent_path else ic.name

            # Build initiator data with visibility windows
            initiators_data = []
            for init in ic.initiators:
                init_data = {
                    'name': init.name,
                    'instance': init.instance or '',
                    'targets': [],
                }
                for init_tgt in init.targets:
                    t = target_map.get(init_tgt.name)
                    if not t:
                        continue

                    tgt_data = {
                        'name': init_tgt.name,
                        'default_base': parent_base + t.base,
                        'size': t.size,
                        'sw_access': t.sw_access,
                        'region_type': t.region_type or '',
                        'instance': t.instance or '',
                        'subordinate_bus': t.subordinate_bus,
                        'desc': t.desc,
                        'windows': [],
                    }

                    if init_tgt.visibility:
                        for vis in init_tgt.visibility:
                            window = {
                                'base': parent_base + (vis.get('base', t.base) if isinstance(vis.get('base'), int) else t.base),
                                'size': t.size,
                            }
                            remap = vis.get('remap')
                            if isinstance(remap, dict) and remap.get('remapping', False):
                                window['remap_bit'] = remap.get('remap_bit', 0)
                                window['remap_behaviour'] = remap.get('remap_behaviour', 'alias')
                            else:
                                window['remap_bit'] = None
                                window['remap_behaviour'] = None
                            tgt_data['windows'].append(window)
                    else:
                        # No visibility — single default window
                        tgt_data['windows'].append({
                            'base': parent_base + t.base,
                            'size': t.size,
                            'remap_bit': None,
                            'remap_behaviour': None,
                        })

                    init_data['targets'].append(tgt_data)
                initiators_data.append(init_data)

            out.append({
                'name': ic.name,
                'path': ic_path,
                'type': ic.type,
                'parent_base': parent_base,
                'top_level': (parent_path == ''),
                'initiators': initiators_data,
            })

        # Recurse into child module instances
        for inst in module.instances:
            if inst.resolved_module and inst.resolved_module.interconnects:
                # Find this instance's base address in parent interconnect
                child_base = parent_base
                for ic in module.interconnects:
                    for t in ic.targets:
                        if t.instance == inst.instance_name:
                            child_base = parent_base + t.base
                            break
                self._collect_interconnect_info(
                    inst.resolved_module,
                    f"{parent_path}.{inst.instance_name}" if parent_path else inst.instance_name,
                    child_base, out
                )

    def _build_assigns_data(self) -> List[Dict[str, Any]]:
        """Build assigns data for visualization."""
        return [
            {
                'target': a.target,
                'expr': a.expr,
                'type': a.type or 'logic',
                'bit': str(a.bit) if a.bit is not None else '',
                'desc': a.desc,
            }
            for a in self.top.assigns
        ]

    def _build_hierarchy(self, module=None, depth=0) -> List[Dict[str, Any]]:
        """Build a tree of the component hierarchy."""
        if module is None:
            module = self.top
        nodes = []
        for inst in module.instances:
            child = inst.resolved_module
            node = {
                'name': inst.instance_name,
                'module': inst.module_name,
                'type': 'rtl_ip' if inst.is_rtl_module else ('subsystem' if inst.module_name.startswith('nanosoc_ss') else 'region'),
                'addressable': inst.addressable,
                'depth': depth,
                'children': [],
            }
            if child and child.instances:
                node['children'] = self._build_hierarchy(child, depth + 1)
            nodes.append(node)
        return nodes

def _inline_data_section(payload: Dict[str, Any]) -> str:
    """Inline data-injection strategy: the eight ``const`` declarations the
    shared viewer code consumes, serialized straight into the page."""
    def j(v):
        return json.dumps(v, indent=2)
    return (
        'const graphData = ' + j(payload['graph']) + ';\n'
        'const allGraphs = ' + j(payload['all_graphs'] or {}) + ';\n'
        'const addressMap = ' + j(payload['address_map']) + ';\n'
        'const initMaps = ' + j(payload['initiator_maps']) + ';\n'
        'const icInfo = ' + j(payload['interconnect_info']) + ';\n'
        'const assignsData = ' + j(payload['assigns']) + ';\n'
        'const hierarchyData = ' + j(payload['hierarchy']) + ';\n'
        'const validationMessages = ' + json.dumps(payload['validation']) + ';'
    )


# Viewer data-injection strategy: a top-level-await bootstrap (hence the
# <script type="module"> opener) that fetches the *_model_view.json payload
# and binds the same eight consts the shared viewer code consumes.
# window.showPanel must be re-exported because module scope (unlike a classic
# script) does not put top-level function declarations on window — the inline
# onclick= handlers in the shared body resolve against window. Function
# declarations are hoisted, so the assignment works before the textual decl.
_VIEWER_BOOTSTRAP = '''\
// Viewer mode: the SoC model data is fetched from a JSON artifact at load.
// NOTE: this page must be served over http(s) — file:// fetch fails (CORS).
window.showPanel = showPanel;
const _payloadUrl = __JSON_URL__;
let _payload;
try {
  const _resp = await fetch(_payloadUrl);
  if (!_resp.ok) throw new Error('HTTP ' + _resp.status + ' ' + _resp.statusText);
  _payload = await _resp.json();
} catch (err) {
  document.body.innerHTML =
    '<div style="font-family:system-ui,sans-serif;max-width:720px;margin:80px auto;color:#e0e0e0">' +
    '<h2 style="color:#e94560">Failed to load SoC model data</h2>' +
    '<p>Could not fetch <code>' + _payloadUrl + '</code>: ' + err + '</p>' +
    '<p>Viewer mode needs this page and its JSON artifact served over ' +
    '<strong>http(s)</strong> — opening via file:// fails (CORS). Generate ' +
    'the build (e.g. <code>make soc</code>) and use the configurator, or ' +
    'open the self-contained <code>*_connectivity.html</code> report instead.</p></div>';
  throw err;
}
const graphData = _payload.graph;
const allGraphs = _payload.all_graphs || {};
const addressMap = _payload.address_map;
const initMaps = _payload.initiator_maps;
const icInfo = _payload.interconnect_info;
const assignsData = _payload.assigns;
const hierarchyData = _payload.hierarchy;
const validationMessages = _payload.validation || [];
{
  const _vbtn = Array.from(document.querySelectorAll('nav button'))
    .find(b => b.textContent.indexOf('Validation') === 0);
  if (_vbtn) _vbtn.textContent = 'Validation (' + validationMessages.length + ')';
}'''


def render_viewer_html(top_name: str, json_url: str, msg_count: int = 0) -> str:
    """Render the viewer-mode page for ``top_name`` loading ``json_url``.

    Module-level (no :class:`Module` needed) so e.g. the configurator can
    render it on the fly from a *_model_view.json artifact's meta alone.
    """
    return _render_page(
        top_name=top_name,
        msg_count=msg_count,
        script_open='<script type="module">',
        data_section=_VIEWER_BOOTSTRAP.replace('__JSON_URL__',
                                               json.dumps(json_url)),
    )


def _render_page(top_name: str, msg_count: int, script_open: str,
                 data_section: str) -> str:
    """Render the complete HTML page.

    ONE template for both modes — only ``script_open`` + ``data_section``
    (the data-injection strategy) differ, so inline and viewer cannot drift.
    """
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{top_name} — Connectivity Visualiser</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
:root {{
  --bg: #1a1a2e; --bg2: #16213e; --bg3: #0f3460; --bg-hover: #2a2a4a;
  --fg: #e0e0e0; --fg2: #adb5bd; --fg3: #6c757d;
  --border: #533483; --accent: #e94560; --bar-bg: #0a0a1a;
}}
body.light {{
  --bg: #f8f9fc; --bg2: #ffffff; --bg3: #eef1f8; --bg-hover: #e8ecf4;
  --fg: #1e293b; --fg2: #475569; --fg3: #94a3b8;
  --border: #cbd5e1; --accent: #4f46e5; --bar-bg: #e2e8f0;
}}
body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--fg); }}

/* Navigation */
nav {{ background: var(--bg2); padding: 10px 20px; display: flex; gap: 10px; align-items: center; border-bottom: 2px solid var(--bg3); }}
nav button {{ background: var(--bg3); color: var(--fg); border: 1px solid var(--border); padding: 8px 16px; cursor: pointer; border-radius: 4px; font-size: 14px; }}
nav button:hover {{ background: var(--border); }}
nav button.active {{ background: var(--border); border-color: var(--accent); }}
nav h1 {{ color: var(--accent); font-size: 18px; margin-right: auto; }}

/* Panels */
.panel {{ display: none; padding: 20px; height: calc(100vh - 50px); overflow: auto; }}
.panel.active {{ display: block; }}

/* Connectivity Visualiser */
#diagram-panel {{ padding: 0; overflow: hidden; position: relative; }}
svg {{ width: 100%; height: 100%; }}
.node rect {{ stroke-width: 2; cursor: grab; rx: 6; ry: 6; }}
.node text {{ fill: var(--fg); font-size: 11px; pointer-events: none; }}
.node .label {{ font-weight: bold; font-size: 13px; }}
.node.expandable rect {{ cursor: pointer; }}
.node.expandable .expand-badge {{ fill: var(--accent); font-size: 10px; pointer-events: none; }}

/* Breadcrumb */
.breadcrumb {{ position: absolute; top: 10px; left: 10px; z-index: 50; display: flex; align-items: center; gap: 4px; background: var(--bg2); border: 1px solid var(--border); padding: 6px 12px; border-radius: 6px; font-size: 13px; box-shadow: 0 2px 8px rgba(0,0,0,0.2); }}
.breadcrumb a {{ color: var(--accent); cursor: pointer; text-decoration: none; }}
.breadcrumb a:hover {{ text-decoration: underline; }}
.breadcrumb .sep {{ color: var(--fg3); margin: 0 2px; }}
.breadcrumb .current {{ color: var(--fg); font-weight: bold; }}
body.light .breadcrumb {{ box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}

/* Zoom controls */
.zoom-controls {{ position: absolute; bottom: 20px; left: 20px; display: flex; flex-direction: column; gap: 4px; z-index: 50; }}
.zoom-controls button {{ width: 32px; height: 32px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg2); color: var(--fg); font-size: 16px; cursor: pointer; display: flex; align-items: center; justify-content: center; }}
.zoom-controls button:hover {{ background: var(--bg-hover); }}
body.light .zoom-controls button {{ box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
.link {{ stroke-opacity: 0.7; fill: none; }}
/* Type determines dash pattern and width */
.link.ahb {{ stroke-width: 3; }}
.link.axis {{ stroke-width: 2; }}
.link.clock {{ stroke-width: 1; stroke-dasharray: 2,2; }}
.link.reset {{ stroke-width: 1; stroke-dasharray: 4,2; }}
.link.wire {{ stroke-width: 1; }}
/* Direction determines colour */
.link.dir-out {{ stroke: #2ecc71; }}
.link.dir-in {{ stroke: #e67e22; }}
.link.dir-bidi {{ stroke: #95a5a6; }}
/* Collapsed (multi-conn off) */
.link.collapsed {{ stroke-width: 3; stroke-opacity: 0.5; }}

/* Tooltip */
.tooltip {{ position: absolute; background: var(--bg2); border: 1px solid var(--border); padding: 10px; border-radius: 6px; font-size: 12px; pointer-events: none; max-width: 350px; z-index: 100; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }}
.tooltip h3 {{ color: var(--accent); margin-bottom: 6px; }}
.tooltip .field {{ color: var(--fg2); }}
.tooltip .value {{ color: var(--fg); }}

/* Address Map Table */
.addr-table {{ width: 100%; border-collapse: collapse; }}
.addr-table th {{ background: var(--bg3); padding: 10px; text-align: left; border-bottom: 2px solid var(--border); }}
.addr-table td {{ padding: 8px 10px; border-bottom: 1px solid var(--bg-hover); }}
.addr-table tr:hover td {{ background: var(--bg-hover); }}
.addr-bar {{ height: 24px; border-radius: 3px; display: inline-block; min-width: 4px; }}
.memory {{ background: #2d6a4f; }}
.periph {{ background: #533483; }}

/* Memory Map Bars (per-initiator view) */
.memmap-card {{ background: var(--bg2); border: 1px solid var(--bg3); border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
.memmap-card h3 {{ color: var(--accent); margin: 0 0 4px 0; font-size: 14px; }}
.memmap-card .sub {{ color: var(--fg2); font-size: 11px; margin-bottom: 10px; }}
.membar {{ position: relative; height: 48px; border: 2px solid var(--border); border-radius: 4px; background: var(--bar-bg); overflow: hidden; }}
.membar .region {{ position: absolute; height: 100%; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: bold; color: #fff; cursor: pointer; transition: opacity 0.2s; border-right: 1px solid #0a0a1a; text-shadow: 0 1px 2px rgba(0,0,0,0.8); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }}
.membar .region:hover {{ opacity: 0.8; }}
.membar .gap {{ position: absolute; height: 100%; background: repeating-linear-gradient(45deg, var(--bg), var(--bg) 4px, var(--bar-bg) 4px, var(--bar-bg) 8px); }}
.memmap-legend {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }}
.memmap-legend-item {{ display: flex; align-items: center; gap: 6px; font-size: 12px; }}
.memmap-legend-color {{ width: 16px; height: 16px; border-radius: 3px; border: 1px solid var(--border); }}
.memmap-axis {{ display: flex; justify-content: space-between; font-size: 10px; color: var(--fg3); margin-top: 4px; font-family: monospace; }}

/* Assigns */
.assign-table {{ width: 100%; border-collapse: collapse; }}
.assign-table th {{ background: var(--bg3); padding: 10px; text-align: left; border-bottom: 2px solid var(--border); }}
.assign-table td {{ padding: 6px 10px; border-bottom: 1px solid var(--bg-hover); font-family: 'Consolas', monospace; font-size: 13px; }}
.assign-table tr:hover td {{ background: var(--bg-hover); }}
.tag {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-family: sans-serif; }}
.tag.interrupt {{ background: var(--accent); color: white; }}
.tag.logic {{ background: var(--bg3); color: var(--fg2); }}

/* Validation */
.msg {{ padding: 6px 12px; margin: 4px 0; border-radius: 4px; font-family: monospace; font-size: 13px; }}
.msg.error {{ background: #3d0000; border-left: 3px solid var(--accent); }}
.msg.warning {{ background: #3d2e00; border-left: 3px solid #ffd60a; }}
.msg.info {{ background: #002a3d; border-left: 3px solid #00b4d8; }}
body.light .msg.error {{ background: #fef2f2; border-left-color: #ef4444; }}
body.light .msg.warning {{ background: #fffbeb; border-left-color: #f59e0b; }}
body.light .msg.info {{ background: #eff6ff; border-left-color: #3b82f6; }}

/* Light mode enhancements */
body.light nav {{ background: #ffffff; border-bottom: 1px solid #e2e8f0; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
body.light nav button {{ background: #eef1f8; border-color: #cbd5e1; color: #475569; }}
body.light nav button:hover {{ background: #dbeafe; border-color: #93c5fd; color: #1e40af; }}
body.light nav button.active {{ background: #4f46e5; border-color: #4f46e5; color: #fff; }}
body.light nav h1 {{ color: #4f46e5; }}
body.light .tooltip {{ background: #ffffff; border: 1px solid #e2e8f0; box-shadow: 0 4px 16px rgba(0,0,0,0.1); }}
body.light .tooltip h3 {{ color: #4f46e5; }}
body.light .memmap-card {{ background: #ffffff; border: 1px solid #e2e8f0; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
body.light .memmap-card h3 {{ color: #4f46e5; }}
body.light .membar {{ border-color: #cbd5e1; background: #f1f5f9; }}
body.light .membar .gap {{ background: repeating-linear-gradient(45deg, #f1f5f9, #f1f5f9 4px, #e2e8f0 4px, #e2e8f0 8px); }}
body.light .membar .region {{ text-shadow: 0 1px 2px rgba(0,0,0,0.4); }}
body.light .addr-table th {{ background: #eef1f8; color: #1e293b; border-bottom-color: #cbd5e1; }}
body.light .addr-table td {{ border-bottom-color: #f1f5f9; }}
body.light .addr-table tr:hover td {{ background: #f8fafc; }}
body.light .assign-table th {{ background: #eef1f8; color: #1e293b; border-bottom-color: #cbd5e1; }}
body.light .assign-table td {{ border-bottom-color: #f1f5f9; }}
body.light .assign-table tr:hover td {{ background: #f8fafc; }}
body.light .tag.interrupt {{ background: #4f46e5; }}
body.light .tag.logic {{ background: #eef1f8; color: #475569; border: 1px solid #cbd5e1; }}
body.light .legend {{ background: #ffffff; border: 1px solid #e2e8f0; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
body.light .controls {{ background: #ffffff; border: 1px solid #e2e8f0; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
body.light .tag-sub-bus {{ color: #0284c7; border-color: #0284c7; }}
body.light .tag-remap {{ color: #b45309; border-color: #b45309; }}

/* Legend */
.legend {{ position: absolute; bottom: 20px; right: 20px; background: var(--bg2); border: 1px solid var(--border); padding: 12px; border-radius: 6px; font-size: 12px; }}
.legend-item {{ display: flex; align-items: center; gap: 8px; margin: 4px 0; }}
.legend-color {{ width: 30px; height: 4px; border-radius: 2px; }}

/* Filter controls */
.controls {{ position: absolute; top: 10px; right: 180px; background: var(--bg2); border: 1px solid var(--border); padding: 10px; border-radius: 6px; font-size: 12px; z-index: 50; }}
.controls label {{ display: inline-block; margin: 2px 8px 2px 0; }}
.controls label {{ display: block; margin: 4px 0; cursor: pointer; }}
.controls input[type="checkbox"] {{ margin-right: 6px; }}

/* Tags in address table */
.tag-sub-bus {{ color: #48cae4; font-size: 10px; border: 1px solid #48cae4; border-radius: 3px; padding: 1px 4px; }}
.tag-remap {{ color: #ffd60a; font-size: 10px; border: 1px solid #ffd60a; border-radius: 3px; padding: 1px 4px; }}
</style>
</head>
<body>
<nav>
  <h1>{top_name} — Connectivity Visualiser</h1>
  <button class="active" onclick="showPanel('diagram')">Connectivity Visualiser</button>
  <button onclick="showPanel('hierarchy')">Hierarchy</button>
  <button onclick="showPanel('memmap')">Memory Maps</button>
  <button onclick="showPanel('address')">Address Table</button>
  <button onclick="showPanel('assigns')">Assigns</button>
  <button onclick="showPanel('validation')">Validation ({msg_count})</button>
  <button onclick="document.body.classList.toggle('light'); if(window._rerenderDiagram) window._rerenderDiagram();" style="margin-left:auto;font-size:12px">\U0001f313 Theme</button>
</nav>

<div id="diagram-panel" class="panel active">
  <div class="breadcrumb" id="breadcrumb"></div>
  <div class="zoom-controls">
    <button onclick="window._zoomIn && window._zoomIn()" title="Zoom in">+</button>
    <button onclick="window._zoomOut && window._zoomOut()" title="Zoom out">&minus;</button>
    <button onclick="window._zoomReset && window._zoomReset()" title="Reset view" style="font-size:12px">&#8634;</button>
  </div>
  <div class="controls">
    <strong>Connection types:</strong>
    <label><input type="checkbox" checked onchange="toggleLinks('ahb')"> AHB</label>
    <label><input type="checkbox" checked onchange="toggleLinks('axis')"> AXI-Stream</label>
    <label><input type="checkbox" checked onchange="toggleLinks('clock')"> Clocks</label>
    <label><input type="checkbox" checked onchange="toggleLinks('reset')"> Resets</label>
    <label><input type="checkbox" checked onchange="toggleLinks('wire')"> Wires</label>
    <br><strong>View:</strong>
    <label><input type="checkbox" id="multi-conn-toggle" onchange="toggleMultiConn()"> Show individual connections</label>
    <label><input type="checkbox" id="port-toggle" onchange="togglePorts()"> Show boundary ports</label>
  </div>
  <div class="legend">
    <strong>Direction</strong>
    <div class="legend-item"><div class="legend-color" style="background:#2ecc71;height:3px"></div> Output / Initiator</div>
    <div class="legend-item"><div class="legend-color" style="background:#e67e22;height:3px"></div> Input / Target</div>
    <div class="legend-item"><div class="legend-color" style="background:#95a5a6;height:3px"></div> Bidirectional</div>
    <hr style="border-color:var(--border);margin:6px 0">
    <strong>Type (dash pattern)</strong>
    <div class="legend-item"><div class="legend-color" style="height:3px;border-top:3px solid var(--fg2)"></div> AHB (solid thick)</div>
    <div class="legend-item"><div class="legend-color" style="height:2px;border-top:2px solid var(--fg2)"></div> AXI-Stream (solid)</div>
    <div class="legend-item"><div class="legend-color" style="height:0;border-top:1px dotted var(--fg2)"></div> Clock (dotted)</div>
    <div class="legend-item"><div class="legend-color" style="height:0;border-top:1px dashed var(--fg2)"></div> Reset (dash-dot)</div>
    <div class="legend-item"><div class="legend-color" style="height:0;border-top:1px solid var(--fg3)"></div> Wire (thin solid)</div>
  </div>
</div>

<div id="hierarchy-panel" class="panel">
  <h2 style="margin-bottom:16px">Component Hierarchy</h2>
  <div id="hierarchy-tree" style="font-family:monospace;font-size:13px;line-height:1.8"></div>
</div>

<div id="memmap-panel" class="panel">
  <h2 style="margin-bottom:16px">Initiator Memory Maps</h2>
  <div id="memmap-remap-controls" style="margin-bottom:12px"></div>
  <div class="memmap-legend" id="memmap-legend"></div>
  <div id="memmap-container"></div>
</div>

<div id="address-panel" class="panel">
  <h2 style="margin-bottom:16px">Address Table</h2>
  <div id="addr-controls" style="margin-bottom:16px;display:flex;flex-wrap:wrap;gap:16px;align-items:center">
    <div style="display:flex;align-items:center;gap:6px;font-size:13px">
      <strong>Initiator:</strong>
      <div id="addr-initiator-toggles" style="display:flex;gap:0;border:1px solid var(--border);border-radius:4px;overflow:hidden"></div>
    </div>
    <div id="addr-remap-controls" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center"></div>
  </div>
  <table class="addr-table" id="addr-table"></table>
</div>

<div id="assigns-panel" class="panel">
  <h2 style="margin-bottom:16px">Combinational Assigns</h2>
  <table class="assign-table" id="assign-table"></table>
</div>

<div id="validation-panel" class="panel">
  <h2 style="margin-bottom:16px">Validation Results</h2>
  <div id="validation-messages"></div>
</div>

<div class="tooltip" id="tooltip" style="display:none"></div>

{script_open}
{data_section}

// Human-readable size formatting
function fmtSize(bytes) {{
  if (bytes >= 1024*1024*1024) return (bytes / (1024*1024*1024)).toFixed(bytes % (1024*1024*1024) === 0 ? 0 : 1) + ' GB';
  if (bytes >= 1024*1024)      return (bytes / (1024*1024)).toFixed(bytes % (1024*1024) === 0 ? 0 : 1) + ' MB';
  if (bytes >= 1024)           return (bytes / 1024).toFixed(bytes % 1024 === 0 ? 0 : 1) + ' KB';
  return bytes + ' B';
}}
function fmtHex(v) {{ return '0x' + v.toString(16).toUpperCase().padStart(8,'0'); }}

// Panel switching
function showPanel(name) {{
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  document.getElementById(name + '-panel').classList.add('active');
  event.target.classList.add('active');
}}

// --- Connectivity Visualiser (with zoom/pan and drill-down) ---
(function() {{
  const panel = document.getElementById('diagram-panel');
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  panel.prepend(svg);
  const width = window.innerWidth;
  const height = window.innerHeight - 50;
  svg.setAttribute('viewBox', `0 0 ${{width}} ${{height}}`);

  const tooltip = document.getElementById('tooltip');
  const breadcrumbEl = document.getElementById('breadcrumb');

  // Track which link types are visible (synced with checkboxes)
  const linkVisible = {{ ahb: true, axis: true, clock: true, reset: true, wire: true }};
  let showMultiConn = false;
  let showPorts = false;

  const isLight = () => document.body.classList.contains('light');
  const darkColors = {{
    top: '#0f3460',
    subsystem: '#1a5276',
    region: '#2d6a4f',
    rtl_ip: '#7b2d8e',
    interconnect: '#c0392b',
    port: '#4a5568',
  }};
  const lightColors = {{
    top: '#3b82f6',
    subsystem: '#2563eb',
    region: '#059669',
    rtl_ip: '#7c3aed',
    interconnect: '#dc2626',
    port: '#6b7280',
  }};
  const colors = new Proxy({{}}, {{ get: (_, k) => (isLight() ? lightColors : darkColors)[k] }});

  const nodeWidth = 220;
  const nodeHeight = 72;
  const portNodeWidth = 140;
  const portNodeHeight = 28;

  // --- Zoom / Pan state ---
  let zoomLevel = 1;
  let panX = 0, panY = 0;
  let isPanning = false, panStartX = 0, panStartY = 0, panStartPanX = 0, panStartPanY = 0;

  // --- Navigation state: stack of {{ moduleName, label }} ---
  let navStack = [{{ moduleName: '{top_name}', label: '{top_name}' }}];

  // Cache for computed layouts per module (so positions persist when navigating back)
  const layoutCache = {{}};

  // Get the graph data for the current view
  function getCurrentGraph() {{
    const current = navStack[navStack.length - 1];
    if (current.moduleName === '{top_name}') return graphData;
    return allGraphs[current.moduleName] || {{ nodes: [], links: [] }};
  }}

  // Compute layout for a set of nodes/links (with caching)
  function getLayout(moduleName) {{
    if (layoutCache[moduleName]) return layoutCache[moduleName];

    const graph = moduleName === '{top_name}' ? graphData : (allGraphs[moduleName] || {{ nodes: [], links: [] }});
    const nodes = graph.nodes.filter(n => n.type !== 'top').map(n => ({{ ...n, vx: 0, vy: 0 }}));
    const nodeMap = {{}};
    nodes.forEach(n => nodeMap[n.id] = n);
    const links = graph.links.filter(l => nodeMap[l.source] && nodeMap[l.target]);

    // Separate port nodes from interior nodes
    const portNodes = nodes.filter(n => n.type === 'port');
    const interiorNodes = nodes.filter(n => n.type !== 'port');

    // Position port nodes on edges (left for inputs, right for outputs)
    const leftPorts = portNodes.filter(n => n.portSide === 'left');
    const rightPorts = portNodes.filter(n => n.portSide === 'right');
    const portMargin = 30;
    const portAreaTop = 60;
    const portAreaBottom = height - 40;

    leftPorts.forEach((n, i) => {{
      n.x = portMargin + portNodeWidth / 2;
      n.y = portAreaTop + (i + 0.5) * (portAreaBottom - portAreaTop) / Math.max(leftPorts.length, 1);
      n.fixed = true;
    }});
    rightPorts.forEach((n, i) => {{
      n.x = width - portMargin - portNodeWidth / 2;
      n.y = portAreaTop + (i + 0.5) * (portAreaBottom - portAreaTop) / Math.max(rightPorts.length, 1);
      n.fixed = true;
    }});

    // Position interior nodes in grid (within the area between port columns)
    const innerLeft = (leftPorts.length > 0 ? portMargin + portNodeWidth + 40 : 100);
    const innerRight = (rightPorts.length > 0 ? width - portMargin - portNodeWidth - 40 : width - 100);
    const innerWidth = innerRight - innerLeft;
    const cx = innerLeft + innerWidth / 2, cy = height / 2;
    const cols = Math.ceil(Math.sqrt(interiorNodes.length)) || 1;
    const rows = Math.ceil(interiorNodes.length / cols) || 1;
    const sx = innerWidth / Math.max(cols - 1, 1);
    const sy = (height - 200) / Math.max(rows - 1, 1);
    interiorNodes.forEach((n, i) => {{
      const col = i % cols;
      const row = Math.floor(i / cols);
      n.x = innerLeft + col * sx;
      n.y = 100 + row * sy;
    }});

    // Force simulation (only on interior nodes)
    for (let iter = 0; iter < 150; iter++) {{
      for (let i = 0; i < nodes.length; i++) {{
        if (nodes[i].fixed) continue;
        for (let j = i + 1; j < nodes.length; j++) {{
          const a = nodes[i], b = nodes[j];
          let dx = b.x - a.x, dy = b.y - a.y;
          let dist = Math.sqrt(dx*dx + dy*dy) || 1;
          let force = 30000 / (dist * dist);
          let fx = dx / dist * force, fy = dy / dist * force;
          if (!a.fixed) {{ a.vx -= fx; a.vy -= fy; }}
          if (!b.fixed) {{ b.vx += fx; b.vy += fy; }}
        }}
      }}
      links.forEach(l => {{
        if (l.type === 'clock' || l.type === 'reset') return;
        const a = nodeMap[l.source], b = nodeMap[l.target];
        if (!a || !b) return;
        let dx = b.x - a.x, dy = b.y - a.y;
        let dist = Math.sqrt(dx*dx + dy*dy) || 1;
        let force = (dist - 350) * 0.003;
        let fx = dx / dist * force, fy = dy / dist * force;
        if (!a.fixed) {{ a.vx += fx; a.vy += fy; }}
        if (!b.fixed) {{ b.vx -= fx; b.vy -= fy; }}
      }});
      nodes.forEach(n => {{
        if (n.fixed) return;
        n.vx += (cx - n.x) * 0.001;
        n.vy += (cy - n.y) * 0.001;
        n.vx *= 0.85; n.vy *= 0.85;
        n.x += n.vx; n.y += n.vy;
        n.x = Math.max(innerLeft + nodeWidth/2 - 60, Math.min(innerRight - nodeWidth/2 + 60, n.x));
        n.y = Math.max(nodeHeight/2 + 10, Math.min(height - nodeHeight/2 - 10, n.y));
      }});
    }}

    const layout = {{ nodes, links, nodeMap }};
    layoutCache[moduleName] = layout;
    return layout;
  }}

  function pairKey(a, b) {{ return [a,b].sort().join('|'); }}
  const dirColors = {{ out: '#2ecc71', in: '#e67e22', bidi: '#95a5a6' }};

  // --- Breadcrumb ---
  function updateBreadcrumb() {{
    let html = '';
    navStack.forEach((entry, i) => {{
      if (i > 0) html += '<span class="sep">/</span>';
      if (i < navStack.length - 1) {{
        html += `<a onclick="window._navTo(${{i}})">${{entry.label}}</a>`;
      }} else {{
        html += `<span class="current">${{entry.label}}</span>`;
      }}
    }});
    breadcrumbEl.innerHTML = html;
  }}

  // Navigate to a specific depth in the stack
  window._navTo = function(idx) {{
    navStack = navStack.slice(0, idx + 1);
    zoomLevel = 1; panX = 0; panY = 0;
    render();
    updateBreadcrumb();
  }};

  // Drill into a child module
  function drillInto(node) {{
    if (!node.module || !allGraphs[node.module]) return;
    navStack.push({{ moduleName: node.module, label: node.label }});
    zoomLevel = 1; panX = 0; panY = 0;
    render();
    updateBreadcrumb();
  }}

  // --- Render ---
  function render() {{
    svg.innerHTML = '';
    const currentModule = navStack[navStack.length - 1].moduleName;
    const layout = getLayout(currentModule);
    const {{ nodes, links, nodeMap }} = layout;

    // Root transform group for zoom/pan
    const rootG = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    rootG.setAttribute('transform', `translate(${{panX}},${{panY}}) scale(${{zoomLevel}})`);
    svg.appendChild(rootG);

    // Arrowhead markers
    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    Object.entries(dirColors).forEach(([dir, color]) => {{
      const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
      marker.setAttribute('id', 'arrow-' + dir);
      marker.setAttribute('viewBox', '0 0 10 6');
      marker.setAttribute('refX', '10');
      marker.setAttribute('refY', '3');
      marker.setAttribute('markerWidth', '8');
      marker.setAttribute('markerHeight', '6');
      marker.setAttribute('orient', 'auto');
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', 'M0,0 L10,3 L0,6 Z');
      path.setAttribute('fill', color);
      marker.appendChild(path);
      defs.appendChild(marker);
    }});
    svg.appendChild(defs);

    const linkGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    rootG.appendChild(linkGroup);

    // Filter visible links (hide port-connected links when ports are hidden)
    const portNodeIds = new Set(nodes.filter(n => n.type === 'port').map(n => n.id));
    const visibleLinks = links.filter(l => {{
      if (!linkVisible[l.type]) return false;
      if (!showPorts && (portNodeIds.has(l.source) || portNodeIds.has(l.target))) return false;
      return true;
    }});

    if (showMultiConn) {{
      const pairIdx = {{}};
      const visCounts = {{}};
      visibleLinks.forEach(l => {{
        const k = pairKey(l.source, l.target);
        visCounts[k] = (visCounts[k] || 0) + 1;
      }});

      visibleLinks.forEach(l => {{
        const a = nodeMap[l.source], b = nodeMap[l.target];
        if (!a || !b) return;
        const k = pairKey(l.source, l.target);
        const total = visCounts[k] || 1;
        pairIdx[k] = (pairIdx[k] || 0) + 1;
        const idx = pairIdx[k];
        const offset = (idx - (total + 1) / 2) * 6;

        let dx = b.x - a.x, dy = b.y - a.y;
        const len = Math.sqrt(dx*dx + dy*dy) || 1;
        const nx = -dy / len * offset, ny = dx / len * offset;

        let x1, y1, x2, y2;
        if (l.direction === 'in') {{
          x1 = b.x + nx; y1 = b.y + ny; x2 = a.x + nx; y2 = a.y + ny;
        }} else {{
          x1 = a.x + nx; y1 = a.y + ny; x2 = b.x + nx; y2 = b.y + ny;
        }}

        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', x1); line.setAttribute('y1', y1);
        line.setAttribute('x2', x2); line.setAttribute('y2', y2);
        line.setAttribute('class', 'link ' + l.type + ' dir-' + l.direction);
        if (l.direction !== 'bidi') {{
          line.setAttribute('marker-end', 'url(#arrow-' + l.direction + ')');
        }}
        const dirLabel = l.direction === 'out' ? '\u2192 output' : l.direction === 'in' ? '\u2190 input' : '\u2194 bidi';
        line.addEventListener('mouseenter', (e) => {{
          tooltip.style.display = 'block';
          tooltip.style.left = e.pageX + 10 + 'px';
          tooltip.style.top = e.pageY + 10 + 'px';
          tooltip.innerHTML = `<h3>Connection</h3>
            <span class="field">Port:</span> <span class="value">${{l.source}}.${{l.port}}</span><br>
            <span class="field">Conn:</span> <span class="value">${{l.conn}}</span><br>
            <span class="field">Type:</span> <span class="value">${{l.type}}</span><br>
            <span class="field">Direction:</span> <span class="value">${{l.portDir}} (${{dirLabel}})</span>`;
        }});
        line.addEventListener('mouseleave', () => tooltip.style.display = 'none');
        linkGroup.appendChild(line);
      }});
    }} else {{
      const pairData = {{}};
      visibleLinks.forEach(l => {{
        const k = pairKey(l.source, l.target);
        if (!pairData[k]) pairData[k] = {{ source: l.source, target: l.target, links: [] }};
        pairData[k].links.push(l);
      }});

      Object.values(pairData).forEach(pd => {{
        const a = nodeMap[pd.source], b = nodeMap[pd.target];
        if (!a || !b) return;
        const count = pd.links.length;

        const outCount = pd.links.filter(l => l.direction === 'out').length;
        const inCount = pd.links.filter(l => l.direction === 'in').length;
        let dir = 'bidi';
        if (outCount > 0 && inCount === 0) dir = 'out';
        else if (inCount > 0 && outCount === 0) dir = 'in';

        const typePri = ['ahb','axis','axis_byte','reset','clock','wire'];
        let bestType = 'wire';
        for (const tp of typePri) {{
          if (pd.links.some(l => l.type === tp)) {{ bestType = tp; break; }}
        }}

        let x1, y1, x2, y2;
        if (dir === 'in') {{
          x1 = b.x; y1 = b.y; x2 = a.x; y2 = a.y;
        }} else {{
          x1 = a.x; y1 = a.y; x2 = b.x; y2 = b.y;
        }}

        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', x1); line.setAttribute('y1', y1);
        line.setAttribute('x2', x2); line.setAttribute('y2', y2);
        line.setAttribute('class', 'link ' + bestType + ' dir-' + dir + ' collapsed');
        if (dir !== 'bidi') {{
          line.setAttribute('marker-end', 'url(#arrow-' + dir + ')');
        }}

        const details = pd.links.map(l => l.source + '.' + l.port + ' \u2192 ' + l.conn + ' (' + l.type + ')').join('<br>');
        line.addEventListener('mouseenter', (e) => {{
          tooltip.style.display = 'block';
          tooltip.style.left = e.pageX + 10 + 'px';
          tooltip.style.top = e.pageY + 10 + 'px';
          tooltip.innerHTML = `<h3>${{count}} connection${{count > 1 ? 's' : ''}}</h3>${{details}}`;
        }});
        line.addEventListener('mouseleave', () => tooltip.style.display = 'none');
        linkGroup.appendChild(line);

        if (count > 1) {{
          const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
          const badge = document.createElementNS('http://www.w3.org/2000/svg', 'g');
          const circ = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
          circ.setAttribute('cx', mx); circ.setAttribute('cy', my); circ.setAttribute('r', 10);
          circ.setAttribute('fill', dirColors[dir]); circ.setAttribute('stroke', 'var(--bg)'); circ.setAttribute('stroke-width', '2');
          const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
          txt.setAttribute('x', mx); txt.setAttribute('y', my + 4);
          txt.setAttribute('text-anchor', 'middle'); txt.setAttribute('fill', '#fff');
          txt.setAttribute('font-size', '10'); txt.setAttribute('font-weight', 'bold');
          txt.textContent = count;
          badge.appendChild(circ); badge.appendChild(txt);
          badge.style.pointerEvents = 'none';
          linkGroup.appendChild(badge);
        }}
      }});
    }}

    // Nodes
    const nodeGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    rootG.appendChild(nodeGroup);

    // Draw module boundary box if there are port nodes and they are visible
    const hasPortNodes = nodes.some(n => n.type === 'port');
    if (hasPortNodes && showPorts) {{
      const boundaryPad = 8;
      const boundaryRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      boundaryRect.setAttribute('x', boundaryPad);
      boundaryRect.setAttribute('y', boundaryPad);
      boundaryRect.setAttribute('width', width - boundaryPad * 2);
      boundaryRect.setAttribute('height', height - boundaryPad * 2);
      boundaryRect.setAttribute('fill', 'none');
      boundaryRect.setAttribute('stroke', colors.port);
      boundaryRect.setAttribute('stroke-width', '2');
      boundaryRect.setAttribute('stroke-dasharray', '12,4');
      boundaryRect.setAttribute('rx', '8');
      boundaryRect.setAttribute('opacity', '0.5');
      rootG.insertBefore(boundaryRect, linkGroup);

      // Module name label on boundary
      const currentModule = navStack[navStack.length - 1];
      const boundaryLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      boundaryLabel.setAttribute('x', width / 2);
      boundaryLabel.setAttribute('y', boundaryPad + 18);
      boundaryLabel.setAttribute('text-anchor', 'middle');
      boundaryLabel.setAttribute('fill', colors.port);
      boundaryLabel.setAttribute('font-size', '13');
      boundaryLabel.setAttribute('font-weight', 'bold');
      boundaryLabel.setAttribute('opacity', '0.7');
      boundaryLabel.textContent = currentModule.moduleName + ' module boundary';
      rootG.insertBefore(boundaryLabel, linkGroup);
    }}

    nodes.forEach(n => {{
      const isPort = n.type === 'port';
      if (isPort && !showPorts) return;  // Skip hidden port nodes
      const nw = isPort ? portNodeWidth : nodeWidth;
      const nh = isPort ? portNodeHeight : nodeHeight;

      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      g.setAttribute('transform', `translate(${{n.x - nw/2}}, ${{n.y - nh/2}})`);

      if (isPort) {{
        // --- Port node rendering ---
        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('width', nw);
        rect.setAttribute('height', nh);
        rect.setAttribute('fill', colors.port);
        rect.setAttribute('rx', '4');
        const portLinkColor = {{ahb: '#e74c3c', apb: '#e67e22', axis: '#27ae60', clock: '#3498db', reset: '#f39c12', wire: '#95a5a6'}}[n.portLinkType] || '#95a5a6';
        rect.setAttribute('stroke', portLinkColor);
        rect.setAttribute('stroke-width', '2');
        rect.setAttribute('class', 'node');
        g.appendChild(rect);

        const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        label.setAttribute('x', nw/2); label.setAttribute('y', 12);
        label.setAttribute('text-anchor', 'middle');
        label.setAttribute('class', 'label');
        label.setAttribute('font-size', '10');
        label.textContent = n.label;
        g.appendChild(label);

        const dirText = n.portDir || '';
        const dirLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        dirLabel.setAttribute('x', nw/2); dirLabel.setAttribute('y', 24);
        dirLabel.setAttribute('text-anchor', 'middle');
        dirLabel.setAttribute('fill', portLinkColor);
        dirLabel.setAttribute('font-size', '8');
        dirLabel.textContent = (n.portIfaceType ? n.portIfaceType + ' ' : '') + dirText;
        g.appendChild(dirLabel);

        // Direction arrow indicator on the edge
        const arrowG = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        if (n.portSide === 'left') {{
          // Arrow pointing inward (from left edge)
          const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
          arrow.setAttribute('points', `${{-8}},${{nh/2-4}} ${{-2}},${{nh/2}} ${{-8}},${{nh/2+4}}`);
          arrow.setAttribute('fill', portLinkColor);
          arrowG.appendChild(arrow);
        }} else {{
          // Arrow pointing outward (to right edge)
          const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
          arrow.setAttribute('points', `${{nw+8}},${{nh/2-4}} ${{nw+2}},${{nh/2}} ${{nw+8}},${{nh/2+4}}`);
          arrow.setAttribute('fill', portLinkColor);
          arrowG.appendChild(arrow);
        }}
        g.appendChild(arrowG);

        // Tooltip for port
        g.addEventListener('mouseenter', (e) => {{
          tooltip.style.display = 'block';
          tooltip.style.left = e.pageX + 10 + 'px';
          tooltip.style.top = e.pageY + 10 + 'px';
          let html = `<h3>Port: ${{n.label}}</h3>`;
          html += `<span class="field">Direction:</span> <span class="value">${{n.portDir || 'unknown'}}</span><br>`;
          if (n.portIfaceType) html += `<span class="field">Interface:</span> <span class="value">${{n.portIfaceType}}</span><br>`;
          if (n.desc) html += `<span class="field">Description:</span> <span class="value">${{n.desc}}</span><br>`;
          html += `<span class="field" style="color:${{portLinkColor}}">External module boundary port</span>`;
          tooltip.innerHTML = html;
        }});
        g.addEventListener('mouseleave', () => tooltip.style.display = 'none');

      }} else {{
        // --- Regular node rendering ---
        const canExpand = n.has_children && n.module && allGraphs[n.module];

        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        rect.setAttribute('width', nw);
        rect.setAttribute('height', nh);
        rect.setAttribute('fill', colors[n.type] || '#333');
        rect.setAttribute('stroke', n.addressable ? '#e94560' : '#533483');
        if (canExpand) {{
          rect.setAttribute('stroke-width', '3');
          rect.setAttribute('stroke-dasharray', '8,3');
        }}
        rect.setAttribute('class', 'node');

        const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        label.setAttribute('x', nw/2); label.setAttribute('y', canExpand ? 20 : 24);
        label.setAttribute('text-anchor', 'middle');
        label.setAttribute('class', 'label');
        label.textContent = n.label;

        const sublabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        sublabel.setAttribute('x', nw/2); sublabel.setAttribute('y', canExpand ? 36 : 42);
        sublabel.setAttribute('text-anchor', 'middle');
        sublabel.setAttribute('fill', '#adb5bd');
        sublabel.setAttribute('font-size', '10');
        sublabel.textContent = n.module || n.bus_type || '';

        const info = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        info.setAttribute('x', nw/2); info.setAttribute('y', canExpand ? 50 : 58);
        info.setAttribute('text-anchor', 'middle');
        info.setAttribute('fill', '#6c757d');
        info.setAttribute('font-size', '9');
        info.textContent = n.type === 'interconnect' ? `${{n.initiators}}I x ${{n.targets}}T` : `${{n.connections || 0}} conn`;

        g.appendChild(rect);
        g.appendChild(label);
        g.appendChild(sublabel);
        g.appendChild(info);

        // Expand indicator for drillable nodes
        if (canExpand) {{
          const expandLabel = document.createElementNS('http://www.w3.org/2000/svg', 'text');
          expandLabel.setAttribute('x', nw/2); expandLabel.setAttribute('y', 64);
          expandLabel.setAttribute('text-anchor', 'middle');
          expandLabel.setAttribute('fill', 'var(--accent)');
          expandLabel.setAttribute('font-size', '10');
          expandLabel.setAttribute('font-weight', 'bold');
          expandLabel.setAttribute('class', 'expand-badge');
          expandLabel.textContent = '\u25B6 click to expand';
          g.appendChild(expandLabel);
          g.setAttribute('class', 'node expandable');
        }}

        // Tooltip
        g.addEventListener('mouseenter', (e) => {{
          tooltip.style.display = 'block';
          tooltip.style.left = e.pageX + 10 + 'px';
          tooltip.style.top = e.pageY + 10 + 'px';
          let html = `<h3>${{n.label}}</h3>`;
          html += `<span class="field">Type:</span> <span class="value">${{n.type}}</span><br>`;
          if (n.module) html += `<span class="field">Module:</span> <span class="value">${{n.module}}</span><br>`;
          if (n.bus_type) html += `<span class="field">Bus:</span> <span class="value">${{n.bus_type}}</span><br>`;
          html += `<span class="field">Interfaces:</span> <span class="value">${{n.interfaces || 0}}</span><br>`;
          html += `<span class="field">Connections:</span> <span class="value">${{n.connections || 0}}</span><br>`;
          if (n.addressable) html += `<span class="field">Addressable:</span> <span class="value">Yes</span><br>`;
          if (n.condition) html += `<span class="field">Condition:</span> <span class="value">${{n.condition}}</span><br>`;
          if (canExpand) html += `<br><span class="field" style="color:var(--accent)">Double-click to view internal connectivity</span>`;
          tooltip.innerHTML = html;
        }});
        g.addEventListener('mouseleave', () => tooltip.style.display = 'none');

        // Double-click to drill into expandable nodes
        if (canExpand) {{
          g.addEventListener('dblclick', (e) => {{
            e.stopPropagation();
            e.preventDefault();
            tooltip.style.display = 'none';
            drillInto(n);
          }});
        }}
      }}

      // Drag (single-click + move)
      let dragging = false, hasMoved = false, ox, oy;
      g.addEventListener('mousedown', (e) => {{
        dragging = true;
        hasMoved = false;
        // Account for zoom/pan: convert screen coords to SVG coords
        ox = (e.clientX - panX) / zoomLevel - n.x;
        oy = (e.clientY - panY) / zoomLevel - n.y;
        e.preventDefault();
        e.stopPropagation();
      }});
      document.addEventListener('mousemove', (e) => {{
        if (!dragging) return;
        hasMoved = true;
        n.x = (e.clientX - panX) / zoomLevel - ox;
        n.y = (e.clientY - panY) / zoomLevel - oy;
        render();
      }});
      document.addEventListener('mouseup', () => {{ dragging = false; }});

      nodeGroup.appendChild(g);
    }});
  }}

  // --- Zoom via mouse wheel ---
  svg.addEventListener('wheel', (e) => {{
    e.preventDefault();
    const rect = svg.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const oldZoom = zoomLevel;
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    zoomLevel = Math.min(5, Math.max(0.1, zoomLevel * delta));

    // Zoom towards mouse position
    panX = mouseX - (mouseX - panX) * (zoomLevel / oldZoom);
    panY = mouseY - (mouseY - panY) * (zoomLevel / oldZoom);

    render();
  }});

  // --- Pan via mouse drag on SVG background ---
  svg.addEventListener('mousedown', (e) => {{
    // Only pan if clicking on the SVG background (not on a node)
    if (e.target === svg || e.target.tagName === 'svg') {{
      isPanning = true;
      panStartX = e.clientX;
      panStartY = e.clientY;
      panStartPanX = panX;
      panStartPanY = panY;
      svg.style.cursor = 'grabbing';
      e.preventDefault();
    }}
  }});
  document.addEventListener('mousemove', (e) => {{
    if (!isPanning) return;
    panX = panStartPanX + (e.clientX - panStartX);
    panY = panStartPanY + (e.clientY - panStartY);
    render();
  }});
  document.addEventListener('mouseup', () => {{
    isPanning = false;
    svg.style.cursor = '';
  }});

  // --- Zoom control buttons ---
  window._zoomIn = function() {{
    const cx = width / 2, cy = height / 2;
    const oldZoom = zoomLevel;
    zoomLevel = Math.min(5, zoomLevel * 1.3);
    panX = cx - (cx - panX) * (zoomLevel / oldZoom);
    panY = cy - (cy - panY) * (zoomLevel / oldZoom);
    render();
  }};
  window._zoomOut = function() {{
    const cx = width / 2, cy = height / 2;
    const oldZoom = zoomLevel;
    zoomLevel = Math.max(0.1, zoomLevel / 1.3);
    panX = cx - (cx - panX) * (zoomLevel / oldZoom);
    panY = cy - (cy - panY) * (zoomLevel / oldZoom);
    render();
  }};
  window._zoomReset = function() {{
    zoomLevel = 1; panX = 0; panY = 0;
    render();
  }};

  // Initial render and breadcrumb
  updateBreadcrumb();
  render();

  // Checkbox-driven link visibility
  window.toggleLinks = function(type) {{
    const cb = event.target;
    linkVisible[type] = cb.checked;
    render();
  }};
  window.toggleMultiConn = function() {{
    showMultiConn = document.getElementById('multi-conn-toggle').checked;
    render();
  }};
  window.togglePorts = function() {{
    showPorts = document.getElementById('port-toggle').checked;
    render();
  }};
  window._rerenderDiagram = render;
}})();

// --- Per-Initiator Memory Maps (with remap toggles and click-to-expand sub-regions) ---
(function() {{
  const container = document.getElementById('memmap-container');
  const legendDiv = document.getElementById('memmap-legend');
  const remapCtrlDiv = document.getElementById('memmap-remap-controls');
  const ADDR_MAX = 0x100000000; // 4 GB
  const tooltip = document.getElementById('tooltip');

  // Shared remap state (synced with address table)
  // We reference the same remapState object from the address table IIFE via window
  // but since we initialise first, create it here and let the addr table share it
  if (!window._memmapRemapState) window._memmapRemapState = {{}};
  const mmRemapState = window._memmapRemapState;

  // Collect global remap bits from icInfo
  const globalRemapBits = new Set();
  icInfo.forEach(ic => {{
    ic.initiators.forEach(init => {{
      init.targets.forEach(tgt => {{
        tgt.windows.forEach(w => {{
          if (w.remap_bit !== null && w.remap_bit !== undefined) {{
            globalRemapBits.add(w.remap_bit);
          }}
        }});
      }});
    }});
  }});
  const sortedUsedBits = [...globalRemapBits].sort((a,b) => a - b);
  const allBits = [0, 1, 2, 3];
  allBits.forEach(b => {{ if (mmRemapState[b] === undefined) mmRemapState[b] = false; }});

  // Build remap toggle UI for memmap panel
  if (sortedUsedBits.length > 0) {{
    const group = document.createElement('div');
    group.style.cssText = 'display:inline-flex;align-items:center;gap:8px;font-size:12px;border:1px solid var(--border);padding:8px 14px;border-radius:6px;background:var(--bg2)';
    group.innerHTML = '<strong style="margin-right:4px">sys_remap_ctrl:</strong>';

    allBits.forEach(bit => {{
      const hasBit = sortedUsedBits.includes(bit);
      const label = document.createElement('label');
      label.style.cssText = 'display:flex;align-items:center;gap:3px;cursor:pointer;user-select:none';
      label.title = hasBit ? 'Remap bit ' + bit : 'Remap bit ' + bit + ' (unused)';

      const toggle = document.createElement('span');
      toggle.style.cssText = 'position:relative;display:inline-block;width:32px;height:18px;border-radius:9px;background:' + (hasBit ? 'var(--bg3)' : 'var(--bg-hover)') + ';transition:background 0.2s;cursor:' + (hasBit ? 'pointer' : 'default') + ';opacity:' + (hasBit ? '1' : '0.35');
      const knob = document.createElement('span');
      knob.style.cssText = 'position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;background:var(--fg2);transition:transform 0.2s,background 0.2s';
      toggle.appendChild(knob);
      toggle._knob = knob;
      toggle._bit = bit;

      if (hasBit) {{
        toggle.addEventListener('click', () => {{
          mmRemapState[bit] = !mmRemapState[bit];
          if (mmRemapState[bit]) {{
            toggle.style.background = 'var(--accent)';
            knob.style.transform = 'translateX(14px)';
            knob.style.background = '#fff';
          }} else {{
            toggle.style.background = 'var(--bg3)';
            knob.style.transform = 'translateX(0)';
            knob.style.background = 'var(--fg2)';
          }}
          rebuildMemoryMaps();
          // Also sync address table remap toggles if they exist
          if (window._syncAddrRemapFromMemmap) window._syncAddrRemapFromMemmap();
        }});
      }}

      label.appendChild(toggle);
      label.appendChild(document.createTextNode('[' + bit + ']'));
      group.appendChild(label);
    }});
    remapCtrlDiv.appendChild(group);
  }}

  // Collect all target names for color assignment
  const targetColors = {{}};
  const palette = ['#e94560','#00b4d8','#2d6a4f','#ffd60a','#533483','#f77f00','#48cae4','#d62828','#06d6a0','#118ab2','#9b59b6','#e67e22','#1abc9c','#34495e'];
  const allTargets = new Set();
  // Gather from both initMaps (basic) and icInfo (with remap windows)
  Object.values(initMaps).forEach(m => m.regions.forEach(r => {{
    allTargets.add(r.name);
    if (r.children) r.children.forEach(c => allTargets.add(c.name));
  }}));
  icInfo.forEach(ic => ic.initiators.forEach(init => init.targets.forEach(tgt => allTargets.add(tgt.name))));
  [...allTargets].forEach((name, i) => targetColors[name] = palette[i % palette.length]);

  // Legend
  function rebuildLegend() {{
    let legendHtml = '';
    allTargets.forEach(name => {{
      legendHtml += `<div class="memmap-legend-item">
        <div class="memmap-legend-color" style="background:${{targetColors[name]}}"></div>
        ${{name}}
      </div>`;
    }});
    legendDiv.innerHTML = legendHtml;
  }}
  rebuildLegend();

  // Tooltip helpers
  function showTip(e, r) {{
    tooltip.style.display = 'block';
    tooltip.style.left = e.pageX + 10 + 'px';
    tooltip.style.top = e.pageY + 10 + 'px';
    let extra = '';
    if (r._hasChildren) extra = '<br><span class=field style="color:#ffd60a">Click to expand/collapse sub-regions</span>';
    let remapInfo = '';
    if (r._remap_bit !== null && r._remap_bit !== undefined) {{
      remapInfo = '<br><span class=field>Remap:</span> <span class=value>bit[' + r._remap_bit + '] ' + (r._remap_behaviour || 'alias') + '</span>';
    }}
    tooltip.innerHTML = '<h3>' + r.name + '</h3>'
      + '<span class=field>Base:</span> <span class=value>' + fmtHex(r.base) + '</span><br>'
      + '<span class=field>End:</span> <span class=value>' + fmtHex(r.end) + '</span><br>'
      + '<span class=field>Size:</span> <span class=value>' + fmtSize(r.size) + ' (' + fmtHex(r.size) + ')</span><br>'
      + '<span class=field>Access:</span> <span class=value>' + (r.sw_access || '') + '</span><br>'
      + '<span class=field>Type:</span> <span class=value>' + (r.region_type || '') + '</span>'
      + remapInfo
      + extra;
  }}
  function moveTip(e) {{ tooltip.style.left = e.pageX+10+'px'; tooltip.style.top = e.pageY+10+'px'; }}
  function hideTip() {{ tooltip.style.display = 'none'; }}

  // Build a bar element from a list of regions
  function buildBar(regions, barHeight) {{
    const bar = document.createElement('div');
    bar.className = 'membar';
    bar.style.height = barHeight + 'px';
    let lastEnd = 0;
    regions.forEach(r => {{
      if (r.base > lastEnd) {{
        const gap = document.createElement('div');
        gap.className = 'gap';
        gap.style.left = (lastEnd / ADDR_MAX * 100).toFixed(6) + '%';
        gap.style.width = ((r.base - lastEnd) / ADDR_MAX * 100).toFixed(6) + '%';
        bar.appendChild(gap);
      }}
      const left = (r.base / ADDR_MAX * 100).toFixed(6);
      const w = (r.size / ADDR_MAX * 100).toFixed(6);
      const color = targetColors[r.name] || '#555';
      const div = document.createElement('div');
      div.className = 'region';
      div.style.left = left + '%';
      div.style.width = w + '%';
      div.style.background = color;
      if (r._remap_bit !== null && r._remap_bit !== undefined) {{
        div.style.backgroundImage = 'repeating-linear-gradient(135deg,transparent,transparent 3px,rgba(255,255,255,0.15) 3px,rgba(255,255,255,0.15) 6px)';
      }}
      if (r._hasChildren) div.style.cursor = 'pointer';
      div.textContent = parseFloat(w) > 2 ? r.name : '';
      div.addEventListener('mouseenter', (e) => showTip(e, r));
      div.addEventListener('mousemove', moveTip);
      div.addEventListener('mouseleave', hideTip);
      div._region = r;
      bar.appendChild(div);
      lastEnd = r.base + r.size;
    }});
    if (lastEnd < ADDR_MAX) {{
      const gap = document.createElement('div');
      gap.className = 'gap';
      gap.style.left = (lastEnd / ADDR_MAX * 100).toFixed(6) + '%';
      gap.style.width = ((ADDR_MAX - lastEnd) / ADDR_MAX * 100).toFixed(6) + '%';
      bar.appendChild(gap);
    }}
    return bar;
  }}

  // Compute visible regions for an initiator using icInfo and current remap state
  // Check if a region_type indicates a real target (not a passthrough)
  function mmIsRealRegionType(rt) {{
    return rt && rt !== '' && rt !== 'None' && rt !== 'none';
  }}

  function computeInitRegions(icEntry, initData) {{
    const regions = [];
    function addWindowRegions(tgtList) {{
      tgtList.forEach(tgt => {{
        tgt.windows.forEach(w => {{
          if (w.remap_bit !== null && w.remap_bit !== undefined) {{
            if (!mmRemapState[w.remap_bit]) return;
          }}
          regions.push({{
            name: tgt.name,
            base: w.base,
            end: w.base + w.size - 1,
            size: w.size,
            sw_access: tgt.sw_access,
            region_type: tgt.region_type,
            instance: tgt.instance,
            _remap_bit: w.remap_bit,
            _remap_behaviour: w.remap_behaviour,
            _hasChildren: false,
            children: [],
          }});
        }});
      }});
    }}
    addWindowRegions(initData.targets);

    // Merge child IC regions when initiator appears in both parent and child
    if (icEntry.top_level) {{
      icInfo.filter(c => !c.top_level).forEach(childIc => {{
        const childInit = childIc.initiators.find(i => i.name === initData.name);
        if (!childInit) return;
        // Only add local targets (those with a real region_type), not passthroughs
        const localTargets = childInit.targets.filter(t => mmIsRealRegionType(t.region_type));
        if (localTargets.length > 0) addWindowRegions(localTargets);
      }});
    }}

    // Remove non-remap regions hidden by active remap overlays
    const remapRegs = regions.filter(r => r._remap_bit !== null && r._remap_bit !== undefined);
    if (remapRegs.length > 0) {{
      const kept = regions.filter(r => {{
        if (r._remap_bit !== null && r._remap_bit !== undefined) return true;
        for (const rr of remapRegs) {{
          const oStart = Math.max(r.base, rr.base);
          const oEnd = Math.min(r.base + r.size, rr.base + rr.size);
          if (oStart < oEnd) return false;
        }}
        return true;
      }});
      regions.length = 0;
      kept.forEach(r => regions.push(r));
    }}

    regions.sort((a, b) => a.base - b.base);

    // Add child sub-regions for subordinate bus targets.
    // Prefer icInfo (remap-aware) over initMaps (static) for children.
    regions.forEach(r => {{
      // First try child interconnects from icInfo (remap-aware)
      icInfo.forEach(childIc => {{
        if (r._hasChildren) return;  // already found children
        if (childIc.top_level) return;
        if (childIc.parent_base < r.base || childIc.parent_base >= r.base + r.size) return;
        const slaveInit = childIc.initiators.find(i => i.name === r.name);
        if (!slaveInit) return;
        const childRegs = [];
        slaveInit.targets.forEach(ct => {{
          ct.windows.forEach(cw => {{
            if (cw.remap_bit !== null && cw.remap_bit !== undefined) {{
              if (!mmRemapState[cw.remap_bit]) return;
            }}
            childRegs.push({{
              name: ct.name,
              base: cw.base,
              end: cw.base + cw.size - 1,
              size: cw.size,
              sw_access: ct.sw_access || '',
              region_type: ct.region_type || '',
              _remap_bit: cw.remap_bit,
              _remap_behaviour: cw.remap_behaviour,
              _hasChildren: false,
            }});
          }});
        }});
        if (childRegs.length > 0) {{
          // Remove non-remap regions hidden by active remap overlays
          const crRemap = childRegs.filter(cr => cr._remap_bit !== null && cr._remap_bit !== undefined);
          let filteredChildren = childRegs;
          if (crRemap.length > 0) {{
            filteredChildren = childRegs.filter(cr => {{
              if (cr._remap_bit !== null && cr._remap_bit !== undefined) return true;
              for (const rr of crRemap) {{
                const oS = Math.max(cr.base, rr.base);
                const oE = Math.min(cr.base + cr.size, rr.base + rr.size);
                if (oS < oE) return false;
              }}
              return true;
            }});
          }}
          r._hasChildren = true;
          r.children = filteredChildren.sort((a,b) => a.base - b.base);
        }}
      }});
      // Fall back to static initMaps children only if icInfo didn't provide any
      if (!r._hasChildren) {{
        Object.values(initMaps).forEach(m => {{
          m.regions.forEach(mr => {{
            if (mr.name === r.name && mr.children && mr.children.length > 0) {{
              r._hasChildren = true;
              r.children = mr.children.map(c => ({{
                name: c.name,
                base: c.base,
                end: c.end,
                size: c.size,
                sw_access: c.sw_access || '',
                region_type: c.region_type || '',
                _remap_bit: null,
                _remap_behaviour: null,
                _hasChildren: false,
              }}));
            }}
          }});
        }});
      }}
    }});
    return regions;
  }}

  // Track which sub-regions are expanded (keyed by "initName::regionName")
  const expandedState = {{}};

  function expansionKey(initName, regionName) {{
    return initName + '::' + regionName;
  }}

  // Rebuild all memory map cards, preserving expansion state
  function rebuildMemoryMaps() {{
    container.innerHTML = '';

    // Use icInfo for top-level initiators (remap-aware)
    const topLevelIcs = icInfo.filter(ic => ic.top_level);
    topLevelIcs.forEach(ic => {{
      ic.initiators.forEach(init => {{
        const regions = computeInitRegions(ic, init);
        if (regions.length === 0) return;

        const card = document.createElement('div');
        card.className = 'memmap-card';

        const heading = document.createElement('h3');
        heading.textContent = init.name.toUpperCase();
        card.appendChild(heading);

        if (init.instance) {{
          const sub = document.createElement('div');
          sub.className = 'sub';
          sub.textContent = 'Instance: ' + init.instance;
          card.appendChild(sub);
        }}

        const mainBar = buildBar(regions, 48);
        card.appendChild(mainBar);

        // Collapsible sub-regions
        regions.forEach(r => {{
          if (!r._hasChildren || !r.children.length) return;
          const children = r.children.slice().sort((a,b) => a.base - b.base);
          const eKey = expansionKey(init.name, r.name);
          const wasExpanded = !!expandedState[eKey];

          const subContainer = document.createElement('div');
          subContainer.style.display = wasExpanded ? 'block' : 'none';
          subContainer.style.marginTop = '4px';
          const label = document.createElement('div');
          label.style.cssText = 'font-size:10px;color:var(--fg3);margin-bottom:2px';
          label.textContent = '\u2514 ' + r.name + ' sub-regions';
          subContainer.appendChild(label);
          const subBar = buildBar(children, 32);
          subContainer.appendChild(subBar);
          card.appendChild(subContainer);

          mainBar.querySelectorAll('.region').forEach(div => {{
            if (div._region === r) {{
              // Restore outline if was expanded
              if (wasExpanded) div.style.outline = '2px solid var(--accent)';
              div.addEventListener('click', () => {{
                const isNowHidden = subContainer.style.display === 'none';
                subContainer.style.display = isNowHidden ? 'block' : 'none';
                div.style.outline = isNowHidden ? '2px solid var(--accent)' : '';
                expandedState[eKey] = isNowHidden;
              }});
            }}
          }});
        }});

        const axis = document.createElement('div');
        axis.className = 'memmap-axis';
        axis.innerHTML = '<span>0x00000000</span><span>0x40000000</span><span>0x80000000</span><span>0xC0000000</span><span>0xFFFFFFFF</span>';
        card.appendChild(axis);
        container.appendChild(card);
      }});
    }});
  }}

  // Initial render
  rebuildMemoryMaps();

  // Expose for external calls
  window.rebuildMemoryMaps = rebuildMemoryMaps;
}})();

// --- Address Map Table (with initiator selection and global remap toggles) ---
(function() {{
  const togglesDiv = document.getElementById('addr-initiator-toggles');
  const remapDiv = document.getElementById('addr-remap-controls');

  // Build flat list of all initiators across all interconnects, and collect remap bits globally
  const allInitiators = [];
  const globalRemapBits = new Set();

  icInfo.forEach(ic => {{
    ic.initiators.forEach(init => {{
      allInitiators.push({{ icName: ic.name, icPath: ic.path, icType: ic.type, topLevel: ic.top_level, init: init }});
      init.targets.forEach(tgt => {{
        tgt.windows.forEach(w => {{
          if (w.remap_bit !== null && w.remap_bit !== undefined) {{
            globalRemapBits.add(w.remap_bit);
          }}
        }});
      }});
    }});
  }});

  // Build initiator toggle buttons — only top-level initiators
  let selectedInitiator = null;
  const topInitiators = allInitiators.filter(e => e.topLevel);
  const toggleBtns = [];

  topInitiators.forEach((entry, idx) => {{
    const btn = document.createElement('button');
    btn.style.cssText = 'padding:6px 14px;border:none;border-right:1px solid var(--border);background:var(--bg3);color:var(--fg);font-size:12px;cursor:pointer;transition:background 0.15s,color 0.15s;white-space:nowrap';
    btn.textContent = entry.init.name;
    btn.title = entry.init.instance ? entry.init.name + ' (' + entry.init.instance + ')' : entry.init.name;
    btn._value = entry.icPath + '::' + entry.init.name;
    btn.addEventListener('click', () => {{
      selectedInitiator = btn._value;
      toggleBtns.forEach(b => {{
        b.style.background = 'var(--bg3)';
        b.style.color = 'var(--fg)';
      }});
      btn.style.background = 'var(--accent)';
      btn.style.color = '#fff';
      rebuildAddrTable();
    }});
    toggleBtns.push(btn);
    togglesDiv.appendChild(btn);
  }});
  // Remove border-right from last button
  if (toggleBtns.length > 0) toggleBtns[toggleBtns.length - 1].style.borderRight = 'none';

  // Default to first initiator (cpu_0)
  if (toggleBtns.length > 0) {{
    selectedInitiator = toggleBtns[0]._value;
    toggleBtns[0].style.background = 'var(--accent)';
    toggleBtns[0].style.color = '#fff';
  }}

  // Build global remap toggle switches (4 bits) — share state with memmap panel
  const remapState = window._memmapRemapState || {{}};
  const allBits = [0, 1, 2, 3];
  const sortedUsedBits = [...globalRemapBits].sort((a,b) => a - b);
  allBits.forEach(b => {{ if (remapState[b] === undefined) remapState[b] = false; }});

  const group = document.createElement('span');
  group.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:12px;border:1px solid var(--border);padding:6px 10px;border-radius:4px;background:var(--bg2)';
  group.innerHTML = '<strong style="margin-right:4px">sys_remap_ctrl:</strong>';

  allBits.forEach(bit => {{
    remapState[bit] = false;
    const hasBit = sortedUsedBits.includes(bit);
    const label = document.createElement('label');
    label.style.cssText = 'display:flex;align-items:center;gap:3px;cursor:pointer;user-select:none';
    label.title = hasBit ? 'Remap bit ' + bit : 'Remap bit ' + bit + ' (unused)';

    // Toggle switch
    const toggle = document.createElement('span');
    toggle.style.cssText = 'position:relative;display:inline-block;width:32px;height:18px;border-radius:9px;background:' + (hasBit ? 'var(--bg3)' : 'var(--bg-hover)') + ';transition:background 0.2s;cursor:' + (hasBit ? 'pointer' : 'default') + ';opacity:' + (hasBit ? '1' : '0.35');
    const knob = document.createElement('span');
    knob.style.cssText = 'position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;background:var(--fg2);transition:transform 0.2s,background 0.2s';
    toggle.appendChild(knob);

    if (hasBit) {{
      toggle.addEventListener('click', () => {{
        remapState[bit] = !remapState[bit];
        if (remapState[bit]) {{
          toggle.style.background = 'var(--accent)';
          knob.style.transform = 'translateX(14px)';
          knob.style.background = '#fff';
        }} else {{
          toggle.style.background = 'var(--bg3)';
          knob.style.transform = 'translateX(0)';
          knob.style.background = 'var(--fg2)';
        }}
        rebuildAddrTable();
        // Also rebuild memory maps since remap state is shared
        if (window.rebuildMemoryMaps) window.rebuildMemoryMaps();
      }});
    }}

    label.appendChild(toggle);
    label.appendChild(document.createTextNode('[' + bit + ']'));
    group.appendChild(label);
  }});
  remapDiv.appendChild(group);

  // Track toggle elements for syncing
  const addrToggleEls = {{}};
  group.querySelectorAll('label').forEach(label => {{
    const toggle = label.querySelector('span');
    if (toggle && toggle.childElementCount === 1) {{
      const knob = toggle.children[0];
      const bitText = label.textContent.match(/\[(\d+)\]/);
      if (bitText) {{
        const b = parseInt(bitText[1]);
        addrToggleEls[b] = {{ toggle, knob }};
      }}
    }}
  }});

  // Sync function: when memmap toggles change, update addr table toggle visuals and rebuild
  window._syncAddrRemapFromMemmap = function() {{
    Object.entries(addrToggleEls).forEach(([bit, els]) => {{
      const b = parseInt(bit);
      if (remapState[b]) {{
        els.toggle.style.background = 'var(--accent)';
        els.knob.style.transform = 'translateX(14px)';
        els.knob.style.background = '#fff';
      }} else {{
        els.toggle.style.background = 'var(--bg3)';
        els.knob.style.transform = 'translateX(0)';
        els.knob.style.background = 'var(--fg2)';
      }}
    }});
    rebuildAddrTable();
  }};

  // Track which groups are expanded, keyed by stable identifier (name:hex_addr)
  const expandedGroups = new Set();

  // Toggle collapse/expand for an address table group
  window.toggleAddrGroup = function(groupIdx) {{
    const rows = document.querySelectorAll('.addr-child-' + groupIdx);
    const icon = document.getElementById('addr-icon-' + groupIdx);
    const key = document.getElementById('addr-icon-' + groupIdx)?.dataset.key;
    if (rows.length === 0) return;
    const isHidden = rows[0].style.display === 'none';
    rows.forEach(row => row.style.display = isHidden ? '' : 'none');
    if (icon) icon.textContent = isHidden ? '\u25BC' : '\u25B6';
    if (key) {{
      if (isHidden) expandedGroups.add(key);
      else expandedGroups.delete(key);
    }}
  }};

  // Render a region row with optional inline register map
  // parentGroupIdx >= 0: this parent row has children (shows collapse icon)
  // childGroupIdx >= 0: this row belongs to that group (hidden by default)
  // groupKey: stable identifier for this group (used to persist expand state across rebuilds)
  function renderRegionRow(r, rowIdx, parentGroupIdx, childGroupIdx, groupKey) {{
    const indent = '&nbsp;'.repeat(r.depth * 4);
    const depthStyle = r.depth > 0 ? 'color:#adb5bd;font-size:12px' : 'font-size:13px';
    const prefix = r.depth > 0 ? '\u2514\u2500 ' : '';
    const hasRegs = r.registers && r.registers.length > 0;
    const regMapLabel = r.register_map || '';

    let tags = '';
    if (r.subordinate_bus) {{
      tags += ' <span style="color:#48cae4;font-size:10px;border:1px solid #48cae4;border-radius:3px;padding:1px 4px">subordinate bus</span>';
    }}
    if (r.remap_bit !== null && r.remap_bit !== undefined) {{
      tags += ' <span style="color:#ffd60a;font-size:10px;border:1px solid #ffd60a;border-radius:3px;padding:1px 4px">remap[' + r.remap_bit + ']</span>';
    }}

    let collapseIcon = '';
    if (parentGroupIdx >= 0) {{
      const dataKey = groupKey ? ` data-key="${{groupKey}}"` : '';
      collapseIcon = `<span id="addr-icon-${{parentGroupIdx}}"${{dataKey}} style="display:inline-block;width:16px;font-size:10px;color:var(--fg3)">\u25B6</span>`;
    }}

    // Build row attributes — a row can be both a child of one group AND a parent of another
    const classAttr = childGroupIdx >= 0 ? `class="addr-child-${{childGroupIdx}}" ` : '';
    const hideStyle = childGroupIdx >= 0 ? 'display:none;' : '';
    const clickAttr = parentGroupIdx >= 0 ? ` onclick="toggleAddrGroup(${{parentGroupIdx}})"` : '';
    const cursorStyle = parentGroupIdx >= 0 ? 'cursor:pointer;' : '';
    const attrs = `${{classAttr}}style="${{hideStyle}}${{cursorStyle}}${{depthStyle}}"${{clickAttr}}`;

    let html = `<tr ${{attrs}}>
      <td>${{indent}}${{prefix}}${{collapseIcon}}<strong>${{r.name}}</strong>${{tags}}</td>
      <td>${{r.instance}}</td>
      <td>${{r.interconnect}}</td>
      <td style="font-family:monospace">${{fmtHex(r.abs_base)}}</td>
      <td style="font-family:monospace">${{fmtHex(r.end)}}</td>
      <td style="font-family:monospace">${{fmtSize(r.size)}}</td>
      <td>${{r.sw_access}}</td>
      <td>${{r.region_type}}</td>
      <td>${{regMapLabel}}</td>
    </tr>`;
    // Register map row — belongs to this row's own group (parentGroupIdx) so
    // clicking the row expands it. Starts hidden; toggleAddrGroup reveals it.
    if (hasRegs) {{
      const regGroup = parentGroupIdx >= 0 ? parentGroupIdx : childGroupIdx;
      let regGroupClass = regGroup >= 0 ? `class="addr-child-${{regGroup}}" ` : '';
      html += `<tr ${{regGroupClass}}style="display:none"><td colspan="9" style="padding:0 0 0 ${{(r.depth + 1) * 24 + 16}}px;background:var(--bg2)">`;
      html += '<table style="width:100%;border-collapse:collapse;font-size:12px;margin:4px 0">';
      html += '<tr style="color:var(--fg2)"><th style="text-align:left;padding:4px 8px">Register</th><th style="text-align:left;padding:4px 8px">Address</th><th style="text-align:left;padding:4px 8px">Offset</th><th style="text-align:left;padding:4px 8px">Access</th><th style="text-align:left;padding:4px 8px">Description</th><th style="text-align:left;padding:4px 8px">Fields</th></tr>';
      r.registers.forEach(reg => {{
        const fieldsStr = reg.fields && reg.fields.length > 0
          ? reg.fields.map(f => f.name + '[' + f.bits + ']').join(', ')
          : '';
        html += `<tr style="border-bottom:1px solid var(--bg-hover)">
          <td style="padding:3px 8px;font-family:monospace;font-weight:bold">${{reg.name}}</td>
          <td style="padding:3px 8px;font-family:monospace">${{fmtHex(reg.abs_addr)}}</td>
          <td style="padding:3px 8px;font-family:monospace">0x${{reg.offset.toString(16).toUpperCase().padStart(3,'0')}}</td>
          <td style="padding:3px 8px">${{reg.access}}</td>
          <td style="padding:3px 8px;max-width:300px">${{reg.desc}}</td>
          <td style="padding:3px 8px;font-family:monospace;font-size:11px;color:var(--fg2)">${{fieldsStr}}</td>
        </tr>`;
      }});
      html += '</table></td></tr>';
    }}
    return html;
  }}

  // Find the address map region data for a target name (for sub-region expansion)
  function findAddrRegions(targetName) {{
    return addressMap.filter(r => r.name === targetName);
  }}

  // Build region objects from an initiator's target windows respecting remap state
  function collectWindowRegions(icName, init, depth) {{
    const regions = [];
    init.targets.forEach(tgt => {{
      tgt.windows.forEach(w => {{
        if (w.remap_bit !== null && w.remap_bit !== undefined) {{
          const remapActive = remapState[w.remap_bit] || false;
          if (!remapActive) return;
        }}
        regions.push({{
          name: tgt.name,
          instance: tgt.instance,
          base: w.base,
          abs_base: w.base,
          size: w.size,
          end: w.base + w.size - 1,
          sw_access: tgt.sw_access,
          region_type: tgt.region_type,
          subordinate_bus: tgt.subordinate_bus || false,
          desc: tgt.desc,
          interconnect: icName,
          depth: depth,
          children: [],
          register_map: null,
          registers: [],
          remap_bit: w.remap_bit,
          remap_behaviour: w.remap_behaviour,
        }});
      }});
    }});
    return regions;
  }}

  // Check if a region_type indicates a real target (not a passthrough)
  function isRealRegionType(rt) {{
    return rt && rt !== '' && rt !== 'None' && rt !== 'none';
  }}

  // Remove non-remap regions that are fully overlapped by an active remap region.
  // When a remap window is active at a given address, it overrides the default
  // region at that address — so the hidden region should not be shown.
  function removeRemapOverlaps(regions) {{
    const remapRegions = regions.filter(r => r.remap_bit !== null && r.remap_bit !== undefined);
    if (remapRegions.length === 0) return regions;
    return regions.filter(r => {{
      // Keep all remap regions
      if (r.remap_bit !== null && r.remap_bit !== undefined) return true;
      // Check if any active remap region overlaps this non-remap region
      for (const rr of remapRegions) {{
        const overlapStart = Math.max(r.abs_base, rr.abs_base);
        const overlapEnd = Math.min(r.abs_base + r.size, rr.abs_base + rr.size);
        if (overlapStart < overlapEnd) return false;  // overlapped, remove
      }}
      return true;
    }});
  }}

  // Compute effective address regions for a specific initiator given remap state.
  // Merges regions from child interconnects when the same initiator name appears
  // in a child IC (e.g. cpu_0 in both the main and CPU subsystem interconnects).
  function computeInitiatorRegions(entry) {{
    const ic = entry;
    const init = entry.init;

    // Start with this initiator's direct regions
    let regions = collectWindowRegions(ic.icName, init, 0);

    // Check if this initiator also appears in child interconnects.
    // If so, merge the child's local targets in place of passthrough regions.
    if (ic.topLevel) {{
      const childIcs = icInfo.filter(c => !c.top_level);
      childIcs.forEach(childIc => {{
        const childInit = childIc.initiators.find(i => i.name === init.name);
        if (!childInit) return;

        // Collect the child's regions
        const childRegions = collectWindowRegions(childIc.name, childInit, 0);

        // Local targets have a real region_type (memory, periph, etc.).
        // Passthrough targets (e.g. 'system') have region_type None/empty.
        const localRegions = childRegions.filter(r => isRealRegionType(r.region_type));

        if (localRegions.length === 0) return;

        // Add the child's local regions to the merged view
        regions = regions.concat(localRegions);
      }});
    }}

    // Remove non-remap regions hidden by active remap overlays
    regions = removeRemapOverlaps(regions);

    regions.sort((a, b) => a.abs_base - b.abs_base);
    return regions;
  }}

  // Compute sub-regions for a subordinate bus target, walking into child interconnects
  function computeSubordinateBusRegions(parentRegion) {{
    let childRegions = [];
    icInfo.forEach(childIc => {{
      if (childIc.top_level) return;
      if (childIc.parent_base < parentRegion.abs_base ||
          childIc.parent_base >= parentRegion.abs_base + parentRegion.size) return;

      // Find the initiator in this child IC that corresponds to the slave port
      const slaveInit = childIc.initiators.find(i => i.name === parentRegion.name);
      if (!slaveInit) return;

      // Use shared helper, then set depth=1 and filter out passthrough targets
      const regions = collectWindowRegions(childIc.name, slaveInit, 1);
      regions.filter(r => isRealRegionType(r.region_type)).forEach(r => childRegions.push(r));
    }});
    // Remove non-remap regions hidden by active remap overlays
    childRegions = removeRemapOverlaps(childRegions);
    childRegions.sort((a, b) => a.abs_base - b.abs_base);
    return childRegions;
  }}

  // Find nested sub-regions from the full address map for a given top-level region
  function getChildRegions(regionName, regionBase) {{
    return addressMap.filter(r => {{
      if (r.depth === 0) return false;
      // Check if this sub-region falls within the parent region
      return r.abs_base >= regionBase;
    }});
  }}

  window.rebuildAddrTable = function() {{
    const table = document.getElementById('addr-table');
    if (!selectedInitiator) {{ table.innerHTML = ''; return; }}

    let html = '<tr><th>Region</th><th>Instance</th><th>Interconnect</th><th>Base</th><th>End</th><th>Size</th><th>Access</th><th>Type</th><th>Register Map</th></tr>';

    const [icPath, initName] = selectedInitiator.split('::');
    const entry = allInitiators.find(e => e.icPath === icPath && e.init.name === initName);
    if (!entry) {{ table.innerHTML = html; return; }}

    const regions = computeInitiatorRegions(entry);
    let rowIdx = 0;
    let groupIdx = 0;
    const groupKeys = [];  // groupIdx -> stable key

    const topLevelIcNames = new Set(icInfo.filter(ic => ic.top_level).map(ic => ic.name));

    regions.forEach(r => {{
      let children = [];
      if (r.subordinate_bus) {{
        children = computeSubordinateBusRegions(r);
      }} else if (topLevelIcNames.has(r.interconnect)) {{
        children = addressMap.filter(cr =>
          cr.depth > 0 && cr.abs_base >= r.abs_base && cr.abs_base < r.abs_base + r.size
        ).sort((a,b) => a.abs_base - b.abs_base || a.depth - b.depth);
      }}

      const hasRegs = r.registers && r.registers.length > 0;
      const hasChildren = children.length > 0 || hasRegs;
      const thisGroup = hasChildren ? groupIdx++ : -1;
      const key = r.name + ':' + (r.abs_base !== undefined ? r.abs_base.toString(16) : '0');
      if (thisGroup >= 0) groupKeys[thisGroup] = key;

      html += renderRegionRow(r, rowIdx++, thisGroup, -1, key);

      children.forEach(cr => {{
        const childHasRegs = cr.registers && cr.registers.length > 0;
        const childGroup = childHasRegs ? groupIdx++ : -1;
        const childKey = cr.name + ':' + (cr.abs_base !== undefined ? cr.abs_base.toString(16) : '0');
        if (childGroup >= 0) groupKeys[childGroup] = childKey;
        html += renderRegionRow(cr, rowIdx++, childGroup, thisGroup, childKey);
      }});
    }});

    table.innerHTML = html;

    // Restore previously expanded groups
    groupKeys.forEach((key, idx) => {{
      if (key && expandedGroups.has(key)) {{
        toggleAddrGroup(idx);
      }}
    }});
  }};

  // Initial render
  rebuildAddrTable();
}})();

// --- Assigns Table ---
(function() {{
  const table = document.getElementById('assign-table');
  let html = '<tr><th>Target</th><th>Expression</th><th>Type</th><th>Bit</th><th>Description</th></tr>';
  assignsData.forEach(a => {{
    const tagCls = a.type === 'interrupt' ? 'interrupt' : 'logic';
    html += `<tr>
      <td>${{a.target}}</td>
      <td>${{a.expr}}</td>
      <td><span class="tag ${{tagCls}}">${{a.type}}</span></td>
      <td>${{a.bit}}</td>
      <td>${{a.desc}}</td>
    </tr>`;
  }});
  table.innerHTML = html;
}})();

// --- Hierarchy Tree ---
(function() {{
  const container = document.getElementById('hierarchy-tree');
  const typeColors = {{ subsystem: '#1a5276', region: '#2d6a4f', rtl_ip: '#7b2d8e' }};
  function renderNode(node, prefix, isLast) {{
    const connector = prefix === '' ? '' : (isLast ? '\u2514\u2500 ' : '\u251c\u2500 ');
    const color = typeColors[node.type] || '#555';
    const badge = node.addressable ? ' <span style="color:var(--accent);font-size:10px">[addr]</span>' : '';
    const line = document.createElement('div');
    line.innerHTML = `<span style="color:var(--fg3)">${{prefix}}${{connector}}</span>`
      + `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${{color}};margin-right:6px;vertical-align:middle"></span>`
      + `<strong>${{node.name}}</strong>`
      + ` <span style="color:var(--fg2)">(${{node.module}})</span>`
      + badge;
    line.style.cursor = node.children.length > 0 ? 'pointer' : 'default';
    container.appendChild(line);

    const childPrefix = prefix + (prefix === '' ? '' : (isLast ? '   ' : '\u2502  '));
    const childContainer = document.createElement('div');
    childContainer.style.display = 'block';
    node.children.forEach((child, i) => {{
      renderNodeInto(child, childPrefix, i === node.children.length - 1, childContainer);
    }});
    container.appendChild(childContainer);

    if (node.children.length > 0) {{
      line.addEventListener('click', () => {{
        childContainer.style.display = childContainer.style.display === 'none' ? 'block' : 'none';
      }});
    }}
  }}
  function renderNodeInto(node, prefix, isLast, parent) {{
    const connector = isLast ? '\u2514\u2500 ' : '\u251c\u2500 ';
    const color = typeColors[node.type] || '#555';
    const badge = node.addressable ? ' <span style="color:var(--accent);font-size:10px">[addr]</span>' : '';
    const line = document.createElement('div');
    line.innerHTML = `<span style="color:var(--fg3)">${{prefix}}${{connector}}</span>`
      + `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${{color}};margin-right:6px;vertical-align:middle"></span>`
      + `<strong>${{node.name}}</strong>`
      + ` <span style="color:var(--fg2)">(${{node.module}})</span>`
      + badge;
    parent.appendChild(line);

    if (node.children.length > 0) {{
      const childPrefix = prefix + (isLast ? '   ' : '\u2502  ');
      const childContainer = document.createElement('div');
      node.children.forEach((child, i) => {{
        renderNodeInto(child, childPrefix, i === node.children.length - 1, childContainer);
      }});
      parent.appendChild(childContainer);
      line.style.cursor = 'pointer';
      line.addEventListener('click', (e) => {{
        e.stopPropagation();
        childContainer.style.display = childContainer.style.display === 'none' ? 'block' : 'none';
      }});
    }}
  }}
  // Root label
  const root = document.createElement('div');
  root.innerHTML = `<span style="display:inline-block;width:12px;height:12px;border-radius:2px;background:#0f3460;margin-right:6px;vertical-align:middle"></span><strong style="font-size:15px">${{graphData.nodes[0]?.label || '{top_name}'}}</strong> <span style="color:var(--fg2)">(top)</span>`;
  root.style.marginBottom = '8px';
  container.appendChild(root);
  hierarchyData.forEach((node, i) => renderNode(node, '', i === hierarchyData.length - 1));
}})();

// --- Validation Messages ---
(function() {{
  const container = document.getElementById('validation-messages');
  if (validationMessages.length === 0) {{
    container.innerHTML = '<div class="msg info">No validation issues found.</div>';
    return;
  }}
  validationMessages.forEach(m => {{
    const cls = m.includes('[ERROR]') ? 'error' : m.includes('[WARNING]') ? 'warning' : 'info';
    const div = document.createElement('div');
    div.className = 'msg ' + cls;
    div.textContent = m;
    container.appendChild(div);
  }});
}})();
</script>
</body>
</html>'''
