#!/usr/bin/env python
#-*- coding:utf-8 -*-

#
# Copyright (C) 2013 Fabrice Desclaux
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#


import miasm2.expression.expression as m2_expr
from miasm2.expression.expression_helper import get_missing_interval
from miasm2.expression.simplifications import expr_simp
from miasm2.core.asmbloc import asm_symbol_pool, expr_is_label, asm_label, \
    asm_bloc
from miasm2.core.graph import DiGraph


class irbloc(object):

    def __init__(self, label, irs, lines=None):
        assert(isinstance(label, asm_label))
        if lines is None:
            lines = []
        self.label = label
        self.irs = irs
        self.lines = lines
        self.except_automod = True
        self._dst = None
        self._dst_linenb = None

    def _get_dst(self):
        """Find the IRDst affectation and update dst, dst_linenb accordingly"""
        if self._dst is not None:
            return self._dst
        dst = None
        for linenb, ir in enumerate(self.irs):
            for i in ir:
                if isinstance(i.dst, m2_expr.ExprId) and i.dst.name == "IRDst":
                    if dst is not None:
                        raise ValueError('Multiple destinations!')
                    dst = i.src
        self._dst = dst
        self._dst_linenb = linenb
        return dst

    def _set_dst(self, value):
        """Find and replace the IRDst affectation's source by @value"""
        if self._dst_linenb is None:
            self._get_dst()

        ir = self.irs[self._dst_linenb]
        for i, expr in enumerate(ir):
            if isinstance(expr.dst, m2_expr.ExprId) and expr.dst.name == "IRDst":
                ir[i] = m2_expr.ExprAff(expr.dst, value)
        self._dst = value

    dst = property(_get_dst, _set_dst)

    @property
    def dst_linenb(self):
        """Line number of the IRDst setting statement in the current irs"""
        return self._dst_linenb

    def get_rw(self, regs_ids):
        """
        Computes the variables read and written by each instructions
        Initialize attributes needed for in/out and reach computation.
        @regs_ids : ids of registers used in IR
        """
        self.r = []
        self.w = []
        self.cur_reach = [{reg: set() for reg in regs_ids}
                          for _ in xrange(len(self.irs))]
        self.prev_reach = [{reg: set() for reg in regs_ids}
                           for _ in xrange(len(self.irs))]
        self.cur_kill = [{reg: set() for reg in regs_ids}
                         for _ in xrange(len(self.irs))]
        self.prev_kill = [{reg: set() for reg in regs_ids}
                          for _ in xrange(len(self.irs))]
        self.defout = [{reg: set() for reg in regs_ids}
                       for _ in xrange(len(self.irs))]

        for k, ir in enumerate(self.irs):
            r, w = set(), set()
            for i in ir:
                r.update(x for x in i.get_r(True)
                         if isinstance(x, m2_expr.ExprId))
                w.update(x for x in i.get_w()
                         if isinstance(x, m2_expr.ExprId))
                if isinstance(i.dst, m2_expr.ExprMem):
                    r.update(x for x in i.dst.arg.get_r(True)
                             if isinstance(x, m2_expr.ExprId))
                self.defout[k].update((x, {(self.label, k, i)})
                                      for x in i.get_w()
                                      if isinstance(x, m2_expr.ExprId))
            self.r.append(r)
            self.w.append(w)

    def __str__(self):
        o = []
        o.append('%s' % self.label)
        for expr in self.irs:
            for e in expr:
                o.append('\t%s' % e)
            o.append("")

        return "\n".join(o)


class DiGraphIR(DiGraph):

    """DiGraph for IR instances"""

    def __init__(self, blocks, *args, **kwargs):
        """Instanciate a DiGraphIR
        @blocks: IR blocks
        """
        self._blocks = blocks
        super(DiGraphIR, self).__init__(*args, **kwargs)

    def node2lines(self, node):
        yield self.DotCellDescription(text=str(node.name),
                                      attr={'align': 'center',
                                            'colspan': 2,
                                            'bgcolor': 'grey'})
        if node not in self._blocks:
            yield [self.DotCellDescription(text="NOT PRESENT", attr={})]
            raise StopIteration
        for i, exprs in enumerate(self._blocks[node].irs):
            for expr in exprs:
                if self._dot_offset:
                    yield [self.DotCellDescription(text="%-4d" % i, attr={}),
                           self.DotCellDescription(text=str(expr), attr={})]
                else:
                    yield self.DotCellDescription(text=str(expr), attr={})
            yield self.DotCellDescription(text="", attr={})

    def edge_attr(self, src, dst):
        if src not in self._blocks or dst not in self._blocks:
            return {}
        src_irdst = self._blocks[src].dst
        edge_color = "blue"
        if isinstance(src_irdst, m2_expr.ExprCond):
            if (expr_is_label(src_irdst.src1) and
                    src_irdst.src1.name == dst):
                edge_color = "limegreen"
            elif (expr_is_label(src_irdst.src2) and
                  src_irdst.src2.name == dst):
                edge_color = "red"
        return {"color": edge_color}

    def node_attr(self, node):
        if node not in self._blocks:
            return {'style': 'filled', 'fillcolor': 'red'}
        return {}

    def dot(self, offset=False):
        """
        @offset: (optional) if set, add the corresponding line number in each
        node
        """
        self._dot_offset = offset
        return super(DiGraphIR, self).dot()


class ir(object):

    def __init__(self, arch, attrib, symbol_pool=None):
        if symbol_pool is None:
            symbol_pool = asm_symbol_pool()
        self.symbol_pool = symbol_pool
        self.blocs = {}
        self.pc = arch.getpc(attrib)
        self.sp = arch.getsp(attrib)
        self.arch = arch
        self.attrib = attrib
        # Lazy structure
        self._graph = None

    def instr2ir(self, l):
        ir_bloc_cur, ir_blocs_extra = self.get_ir(l)
        return ir_bloc_cur, ir_blocs_extra

    def get_label(self, ad):
        """Transforms an ExprId/ExprInt/label/int into a label
        @ad: an ExprId/ExprInt/label/int"""

        if (isinstance(ad, m2_expr.ExprId) and
                isinstance(ad.name, asm_label)):
            ad = ad.name
        if isinstance(ad, m2_expr.ExprInt):
            ad = int(ad.arg)
        if type(ad) in [int, long]:
            ad = self.symbol_pool.getby_offset_create(ad)
        elif isinstance(ad, asm_label):
            ad = self.symbol_pool.getby_name_create(ad.name)
        return ad

    def get_bloc(self, ad):
        """Returns the irbloc associated to an ExprId/ExprInt/label/int
        @ad: an ExprId/ExprInt/label/int"""

        label = self.get_label(ad)
        return self.blocs.get(label, None)

    def add_instr(self, l, ad=0, gen_pc_updt=False):
        b = asm_bloc(self.gen_label())
        b.lines = [l]
        self.add_bloc(b, gen_pc_updt)

    def merge_multi_affect(self, affect_list):
        """
        If multiple affection to a same ExprId are present in @affect_list,
        merge them (in place).
        For instance, XCGH AH, AL semantic is
        [
            RAX = {RAX[0:8],0,8, RAX[0:8],8,16, RAX[16:64],16,64}
            RAX = {RAX[8:16],0,8, RAX[8:64],8,64}
        ]
        This function will update @affect_list to replace previous ExprAff by
        [
            RAX = {RAX[8:16],0,8, RAX[0:8],8,16, RAX[16:64],16,64}
        ]
        """

        # Extract side effect
        effect = {}
        for expr in affect_list:
            effect[expr.dst] = effect.get(expr.dst, []) + [expr]

        # Find candidates
        for dst, expr_list in effect.items():
            if len(expr_list) <= 1:
                continue

            # Only treat ExprCompose list
            if any(map(lambda e: not(isinstance(e.src, m2_expr.ExprCompose)),
                       expr_list)):
                continue

            # Find collision
            e_colision = reduce(lambda x, y: x.union(y),
                                (e.get_modified_slice() for e in expr_list),
                                set())
            # Sort interval collision
            known_intervals = sorted([(x[1], x[2]) for x in e_colision])

            # Fill with missing data
            missing_i = get_missing_interval(known_intervals, 0, dst.size)

            remaining = ((m2_expr.ExprSlice(dst, *interval),
                          interval[0],
                          interval[1])
                         for interval in missing_i)

            # Build the merging expression
            slices = sorted(e_colision.union(remaining), key=lambda x: x[1])
            final_dst = m2_expr.ExprCompose(slices)

            # Remove unused expression
            for expr in expr_list:
                affect_list.remove(expr)

            # Add the merged one
            affect_list.append(m2_expr.ExprAff(dst, final_dst))

    def getby_offset(self, offset):
        out = set()
        for irb in self.blocs.values():
            for l in irb.lines:
                if l.offset <= offset < l.offset + l.l:
                    out.add(irb)
        return out

    def gen_pc_update(self, c, l):
        c.irs.append([m2_expr.ExprAff(self.pc, m2_expr.ExprInt_from(self.pc,
                                                                    l.offset))])
        c.lines.append(l)

    def add_bloc(self, bloc, gen_pc_updt=False):
        c = None
        ir_blocs_all = []
        for l in bloc.lines:
            if c is None:
                label = self.get_instr_label(l)
                c = irbloc(label, [], [])
                ir_blocs_all.append(c)
            ir_bloc_cur, ir_blocs_extra = self.instr2ir(l)

            if gen_pc_updt is not False:
                self.gen_pc_update(c, l)

            c.irs.append(ir_bloc_cur)
            c.lines.append(l)

            if ir_blocs_extra:
                for b in ir_blocs_extra:
                    b.lines = [l] * len(b.irs)
                ir_blocs_all += ir_blocs_extra
                c = None
        self.post_add_bloc(bloc, ir_blocs_all)
        return ir_blocs_all

    def expr_fix_regs_for_mode(self, e, *args, **kwargs):
        return e

    def expraff_fix_regs_for_mode(self, e, *args, **kwargs):
        return e

    def irbloc_fix_regs_for_mode(self, irbloc, *args, **kwargs):
        return

    def is_pc_written(self, b):
        all_pc = self.arch.pc.values()
        for irs in b.irs:
            for ir in irs:
                if ir.dst in all_pc:
                    return ir
        return None

    def set_empty_dst_to_next(self, bloc, ir_blocs):
        for b in ir_blocs:
            if b.dst is not None:
                continue
            dst = m2_expr.ExprId(self.get_next_label(bloc.lines[-1]),
                                 self.pc.size)
            b.irs.append([m2_expr.ExprAff(self.IRDst, dst)])
            b.lines.append(b.lines[-1])

    def gen_edges(self, bloc, ir_blocs):
        pass

    def post_add_bloc(self, bloc, ir_blocs):
        self.set_empty_dst_to_next(bloc, ir_blocs)
        self.gen_edges(bloc, ir_blocs)

        for irb in ir_blocs:
            self.irbloc_fix_regs_for_mode(irb, self.attrib)

            # Detect multi-affectation
            for affect_list in irb.irs:
                self.merge_multi_affect(affect_list)

            self.blocs[irb.label] = irb

        # Forget graph if any
        self._graph = None

    def get_instr_label(self, instr):
        """Returns the label associated to an instruction
        @instr: current instruction"""

        return self.symbol_pool.getby_offset_create(instr.offset)

    def gen_label(self):
        # TODO: fix hardcoded offset
        l = self.symbol_pool.gen_label()
        return l

    def get_next_label(self, instr):
        l = self.symbol_pool.getby_offset_create(instr.offset + instr.l)
        return l

    def simplify_blocs(self):
        for b in self.blocs.values():
            for ir in b.irs:
                for i, r in enumerate(ir):
                    ir[i] = m2_expr.ExprAff(expr_simp(r.dst), expr_simp(r.src))

    def replace_expr_in_ir(self, bloc, rep):
        for irs in bloc.irs:
            for i, l in enumerate(irs):
                irs[i] = l.replace_expr(rep)

    def get_rw(self, regs_ids=None):
        """
        Calls get_rw(irb) for each bloc
        @regs_ids : ids of registers used in IR
        """
        if regs_ids is None:
            regs_ids = []
        for b in self.blocs.values():
            b.get_rw(regs_ids)

    def sort_dst(self, todo, done):
        out = set()
        while todo:
            dst = todo.pop()
            if expr_is_label(dst):
                done.add(dst)
            elif isinstance(dst, m2_expr.ExprMem) or isinstance(dst, m2_expr.ExprInt):
                done.add(dst)
            elif isinstance(dst, m2_expr.ExprCond):
                todo.add(dst.src1)
                todo.add(dst.src2)
            elif isinstance(dst, m2_expr.ExprId):
                out.add(dst)
            else:
                done.add(dst)
        return out

    def dst_trackback(self, b):
        dst = b.dst
        todo = set([dst])
        done = set()

        for irs in reversed(b.irs):
            if len(todo) == 0:
                break
            out = self.sort_dst(todo, done)
            found = set()
            follow = set()
            for i in irs:
                if not out:
                    break
                for o in out:
                    if i.dst == o:
                        follow.add(i.src)
                        found.add(o)
                for o in found:
                    out.remove(o)

            for o in out:
                if o not in found:
                    follow.add(o)
            todo = follow

        return done

    def _gen_graph(self):
        """
        Gen irbloc digraph
        """
        self._graph = DiGraphIR(self.blocs)
        for lbl, b in self.blocs.iteritems():
            self._graph.add_node(lbl)
            dst = self.dst_trackback(b)
            for d in dst:
                if isinstance(d, m2_expr.ExprInt):
                    d = m2_expr.ExprId(
                        self.symbol_pool.getby_offset_create(int(d.arg)))
                if expr_is_label(d):
                    self._graph.add_edge(lbl, d.name)

    @property
    def graph(self):
        """Get a DiGraph representation of current IR instance.
        Lazy property, building the graph on-demand"""
        if self._graph is None:
            self._gen_graph()
        return self._graph
