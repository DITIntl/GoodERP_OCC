# -*- coding: utf-8 -*-

from datetime import date, datetime, timedelta

from odoo import api, fields, models, SUPERUSER_ID, _
from odoo.exceptions import UserError
from odoo.tools import DEFAULT_SERVER_DATE_FORMAT, DEFAULT_SERVER_DATETIME_FORMAT


class MaintenanceStage(models.Model):
    """ Model for case stages. This models the main stages of a Maintenance Request management flow. """

    _name = 'my_equipment_maintenance.stage'
    _description = '维护阶段'
    _order = 'sequence, id'

    name = fields.Char('Name', required=True, translate=True)
    sequence = fields.Integer('Sequence', default=20)
    fold = fields.Boolean('在维护阶段中折叠')
    done = fields.Boolean('请求完成')


class MaintenanceEquipmentCategory(models.Model):
    _name = 'my_equipment_maintenance.equipment.category'
    _description = '设备维护分类'

    @api.one
    @api.depends('equipment_ids')
    def _compute_fold(self):
        self.fold = False if self.equipment_count else True

    name = fields.Char('分类名称', required=True)
    color = fields.Integer('Color Index')
    note = fields.Text('备注')
    equipment_ids = fields.One2many('my_equipment_maintenance.equipment', 'category_id', string='设备',
                                    copy=False)
    equipment_count = fields.Integer(string="设备", compute='_compute_equipment_count')
    my_equipment_maintenance_ids = fields.One2many('my_equipment_maintenance.request', 'category_id', copy=False)
    my_equipment_maintenance_count = fields.Integer(string="维护个数",
                                                    compute='_compute_my_equipment_maintenance_count')
    fold = fields.Boolean(string='在维护中折叠', compute='_compute_fold', store=True)

    @api.multi
    def _compute_equipment_count(self):
        equipment_data = self.env['my_equipment_maintenance.equipment'].read_group([('category_id', 'in', self.ids)],
                                                                                   ['category_id'], ['category_id'])
        mapped_data = dict([(m['category_id'][0], m['category_id_count']) for m in equipment_data])
        for category in self:
            category.equipment_count = mapped_data.get(category.id, 0)

    @api.multi
    def _compute_my_equipment_maintenance_count(self):
        my_equipment_maintenance_data = self.env['my_equipment_maintenance.request'].read_group([('category_id', 'in', self.ids)],
                                                                                   ['category_id'], ['category_id'])
        mapped_data = dict([(m['category_id'][0], m['category_id_count']) for m in my_equipment_maintenance_data])
        for category in self:
            category.my_equipment_maintenance_count = mapped_data.get(category.id, 0)


    @api.multi
    def unlink(self):
        MailAlias = self.env['mail.alias']
        for category in self:
            if category.equipment_ids or category.my_equipment_maintenance_ids:
                raise UserError(_("你不能删除一个包含设备维护请求或设备的分类！"))
            MailAlias += category.alias_id
        res = super(MaintenanceEquipmentCategory, self).unlink()
        MailAlias.unlink()
        return res


class MaintenanceEquipment(models.Model):
    _name = 'my_equipment_maintenance.equipment'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = '设备'


    @api.multi
    def name_get(self):
        result = []
        for record in self:
            if record.name and record.serial_no:
                result.append((record.id, record.name + '/' + record.serial_no))
            if record.name and not record.serial_no:
                result.append((record.id, record.name))
        return result

    @api.model
    def _name_search(self, name, args=None, operator='ilike', limit=100, name_get_uid=None):
        args = args or []
        equipment_ids = []
        if name:
            equipment_ids = self._search([('name', '=', name)] + args, limit=limit, access_rights_uid=name_get_uid)
        if not equipment_ids:
            equipment_ids = self._search([('name', operator, name)] + args, limit=limit, access_rights_uid=name_get_uid)
        return self.browse(equipment_ids).name_get()

    name = fields.Char('设备名称', required=True)
    active = fields.Boolean(default=True)
    technician_user_id = fields.Many2one('res.users', string='维护人', track_visibility='onchange')
    owner_user_id = fields.Many2one('res.users', string='操作者', track_visibility='onchange')
    category_id = fields.Many2one('my_equipment_maintenance.equipment.category', string='设备分类',
                                  track_visibility='onchange', group_expand='_read_group_category_ids')
    is_environmental_protecting_equipment = fields.Boolean('是否环保设备', default=False)
    partner_id = fields.Many2one('res.partner', string='供应商', domain="[('supplier', '=', 1)]")
    location = fields.Char('安装位置')
    model = fields.Char('型号')
    serial_no = fields.Char('设备编号', copy=False)
    production_date = fields.Date('生产日期')
    equipment_power = fields.Integer('设备功率(KW)')
    effective_date = fields.Date('投入使用日期', default=fields.Date.context_today, required=True,
                                 help="设备投入使用日期，用于按维护频次计算维护时间")
    cost = fields.Float('价格（元）')
    note = fields.Text('备注')
    warranty_date = fields.Date('质保期失效日期', oldname='warranty')
    color = fields.Integer('颜色索引')
    scrap_date = fields.Date('报废日期')
    my_equipment_maintenance_ids = fields.One2many('my_equipment_maintenance.request', 'equipment_id')
    my_equipment_maintenance_count = fields.Integer(compute='_compute_my_equipment_maintenance_count',
                                                    string="维护次数", store=True)
    my_equipment_maintenance_open_count = fields.Integer(compute='_compute_my_equipment_maintenance_count',
                                                         string="当前维护", store=True)
    period = fields.Integer('预防性维护之间的天数')
    next_action_date = fields.Date(compute='_compute_next_my_equipment_maintenance', string='预防性设备维护', store=True)
    my_equipment_maintenance_duration = fields.Float('维护用时', help="维护用时")
    equipment_spare_parts = fields.One2many(
        'equipment_spare_parts.line', 'equipment_register_id', '设备备件清单',copy=True, readonly=False)

    @api.depends('effective_date', 'period', 'my_equipment_maintenance_ids.request_date',
                 'my_equipment_maintenance_ids.close_date')
    def _compute_next_my_equipment_maintenance(self):
        date_now = fields.Date.context_today(self)
        for equipment in self.filtered(lambda x: x.period > 0):
            next_my_equipment_maintenance_todo = self.env['my_equipment_maintenance.request'].search([
                ('equipment_id', '=', equipment.id),
                ('my_equipment_maintenance_type', '=', 'preventive'),
                ('stage_id.done', '!=', True),
                ('close_date', '=', False)], order="request_date asc", limit=1)
            last_my_equipment_maintenance_done = self.env['my_equipment_maintenance.request'].search([
                ('equipment_id', '=', equipment.id),
                ('my_equipment_maintenance_type', '=', 'preventive'),
                ('stage_id.done', '=', True),
                ('close_date', '!=', False)], order="close_date desc", limit=1)
            if next_my_equipment_maintenance_todo and last_my_equipment_maintenance_done:
                next_date = next_my_equipment_maintenance_todo.request_date
                date_gap = next_my_equipment_maintenance_todo.request_date - last_my_equipment_maintenance_done.\
                    close_date
                # If the gap between the last_my_equipment_maintenance_done and
                # the next_my_equipment_maintenance_todo one is bigger than 2 times the period and next
                # request is in the future
                # We use 2 times the period to avoid creation too closed request from a manually one created
                if date_gap > timedelta(0) and date_gap > timedelta(days=equipment.period) * 2 and \
                        next_my_equipment_maintenance_todo.request_date > date_now:
                    # If the new date still in the past, we set it for today
                    if last_my_equipment_maintenance_done.close_date + timedelta(days=equipment.period) < date_now:
                        next_date = date_now
                    else:
                        next_date = last_my_equipment_maintenance_done.close_date + timedelta(days=equipment.period)
            elif next_my_equipment_maintenance_todo:
                next_date = next_my_equipment_maintenance_todo.request_date
                date_gap = next_my_equipment_maintenance_todo.request_date - date_now
                # If next my_equipment_maintenance to do is in the future, and in more than 2 times the period,
                # we insert an new request
                # We use 2 times the period to avoid creation too closed request from a manually one created
                if date_gap > timedelta(0) and date_gap > timedelta(days=equipment.period) * 2:
                    next_date = date_now + timedelta(days=equipment.period)
            elif last_my_equipment_maintenance_done:
                next_date = last_my_equipment_maintenance_done.close_date + timedelta(days=equipment.period)
                # If when we add the period to the last my_equipment_maintenance done and we still in past,
                # we plan it for today
                if next_date < date_now:
                    next_date = date_now
            else:
                next_date = self.effective_date + timedelta(days=equipment.period)
            equipment.next_action_date = next_date

    @api.one
    @api.depends('my_equipment_maintenance_ids.stage_id.done')
    def _compute_my_equipment_maintenance_count(self):
        self.my_equipment_maintenance_count = len(self.my_equipment_maintenance_ids)
        self.my_equipment_maintenance_open_count = len(self.my_equipment_maintenance_ids.filtered(lambda x:
                                                                                                  not x.stage_id.done))

    _sql_constraints = [
        ('serial_no', 'unique(serial_no)', "其它设备已经使用了这个设备编号"),
    ]

    @api.model
    def _read_group_category_ids(self, categories, domain, order):
        """ Read group customization in order to display all the categories in
            the kanban view, even if they are empty.
        """
        category_ids = categories._search([], order=order, access_rights_uid=SUPERUSER_ID)
        return categories.browse(category_ids)

    @api.model
    def _cron_generate_requests(self):
        """
            Generates my_equipment_maintenance request on the next_action_date or today if none exists
        """
        for equipment in self.search([('period', '>', 0)]):
            next_requests = self.env['my_equipment_maintenance.request'].search([('stage_id.done', '=', False),
                                                    ('equipment_id', '=', equipment.id),
                                                    ('my_equipment_maintenance_type', '=', 'preventive'),
                                                    ('request_date', '=', equipment.next_action_date)])
            if not next_requests:
                equipment._create_new_request(equipment.next_action_date)


class MaintenanceRequest(models.Model):
    _name = 'my_equipment_maintenance.request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = '维护请求'
    _order = "id desc"

    @api.returns('self')
    def _default_stage(self):
        return self.env['my_equipment_maintenance.stage'].search([], limit=1)

    name = fields.Char('维修单编号',
        default=lambda self: self.env['ir.sequence'].next_by_code('maintenance.order'),
        copy=False, required=True,states={'new': [('readonly', False)]})
    description = fields.Text('描述')
    internal_notes = fields.Text('备注')
    request_date = fields.Date('请求日期', track_visibility='onchange', default=fields.Date.context_today,
                               help="要求维修实施的时间。")
    owner_user_id = fields.Many2one('res.users', string='创建人', default=lambda s: s.env.uid)
    category_id = fields.Many2one('my_equipment_maintenance.equipment.category', related='equipment_id.category_id',
                                  string='分类', store=True, readonly=True)
    equipment_id = fields.Many2one('my_equipment_maintenance.equipment', string='设备',
                                   ondelete='restrict', index=True, required=True)
    user_id = fields.Many2many('hr.employee', string='维护人', ondelete='restrict', track_visibility='onchange')
    stage_id = fields.Many2one('my_equipment_maintenance.stage', string='阶段', ondelete='restrict',
                               track_visibility='onchange',
                               group_expand='_read_group_stage_ids', default=_default_stage)
    priority = fields.Selection([('0', '很低'), ('1', '低'), ('2', '一般'), ('3', '高')], string='优先级')
    color = fields.Integer('颜色索引')
    close_date = fields.Date('关闭日期', help="Date the my_equipment_maintenance was finished. ")
    # active = fields.Boolean(default=True, help="Set active to false to hide the my_equipment_maintenance request
    # without deleting it.")
    archive = fields.Boolean(default=False, help="使用存档来将维护请求在不删除的情况下不可见。")
    my_equipment_maintenance_type = fields.Selection([('corrective', '纠正'), ('preventive', '预防')],
                                                     string='维护类型', default="preventive")
    schedule_date = fields.Date('计划日期', help="维护团队期望的实施日期")
    duration = fields.Float('用时', help="用小时和分钟表示的时间")
    operations = fields.One2many('maintenance.line', 'maintenance_id', '零件',copy=True, readonly=False)
    kanban_state = fields.Selection([('normal', 'In Progress'), ('blocked', 'Blocked'), ('done', 'Ready for next stage')],
                                    string='Kanban State', required=True, default='normal', track_visibility='onchange')


    @api.multi
    def archive_equipment_request(self):
        self.write({'archive': True})


class RepairLine(models.Model):
    _name = 'maintenance.line'
    _description = '维护用零件'
    name = fields.Text('描述')
    maintenance_id = fields.Many2one(
            'my_equipment_maintenance.request', '维护单编号',
            index=True, ondelete='cascade')
    product_id = fields.Many2one('equipment.parts', '产品', required=True)
    product_uom_qty = fields.Integer(
            '数量', default=1, required=True)

class EquipmentPartsLine(models.Model):
    _name = 'equipment_spare_parts.line'
    _description = '设备备件明细'
    equipment_spare_part_id = fields.Many2one('equipment.parts', '产品', required=True)
    equipment_part_qty = fields.Integer(
            '数量', default=1, required=True)
    is_virtual_stock = fields.Boolean('虚拟库存', default=False, help="不在库房存货，存放在供应商处。")
    equipment_register_id = fields.Many2one(
            'my_equipment_maintenance.equipment', '设备',
            index=True, ondelete='cascade')




