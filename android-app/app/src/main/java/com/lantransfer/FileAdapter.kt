package com.lantransfer

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import com.lantransfer.databinding.ItemFileBinding

class FileAdapter(
    private var items: List<FileItem>,
    private val onDownload: (FileItem) -> Unit,
    private val onDelete: (FileItem) -> Unit
) : RecyclerView.Adapter<FileAdapter.VH>() {

    class VH(val b: ItemFileBinding) : RecyclerView.ViewHolder(b.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val b = ItemFileBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return VH(b)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val f = items[position]
        holder.b.tvName.text = f.name
        holder.b.tvSize.text = FileItem.formatSize(f.size) +
            if (f.space.isNotBlank()) " · ${f.space}" else ""
        holder.b.tvKind.text = f.kind.uppercase()
        holder.b.btnDownload.setOnClickListener { onDownload(f) }
        holder.b.btnDelete.setOnClickListener { onDelete(f) }
    }

    override fun getItemCount() = items.size

    fun update(list: List<FileItem>) {
        items = list
        notifyDataSetChanged()
    }
}
